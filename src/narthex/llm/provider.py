"""Model-agnostic LLM provider abstraction.

Every provider returns the same :class:`LLMResponse` shape, so the rest of
Narthex never depends on a particular vendor SDK.  The OpenAI-compatible
provider uses only the standard library, which keeps the project's
"zero third-party runtime dependencies" guarantee intact while working with
OpenAI, DeepSeek, Qwen, Ollama, vLLM and any other OpenAI-style endpoint.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

from ..errors import NarthexError


class LLMProviderError(NarthexError):
    """Raised when a provider call fails after all retries."""


@dataclass(frozen=True)
class LLMResponse:
    """Canonical provider result, independent of the backend."""

    text: str
    provider: str
    model: str
    usage: dict[str, int] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


class LLMProvider(Protocol):
    """Minimal contract every backend must satisfy."""

    provider_id: str

    def complete(
        self,
        prompt: str,
        *,
        system: str = "",
        max_tokens: int = 1024,
        temperature: float = 0.2,
    ) -> LLMResponse: ...


class ScriptedProvider:
    """Returns canned responses in order; used by tests and offline demos."""

    provider_id = "scripted"

    def __init__(self, responses: list[str], model: str = "scripted-model") -> None:
        self._responses = list(responses)
        self._model = model
        self.calls: list[dict[str, Any]] = []

    def complete(
        self,
        prompt: str,
        *,
        system: str = "",
        max_tokens: int = 1024,
        temperature: float = 0.2,
    ) -> LLMResponse:
        self.calls.append(
            {"system": system, "prompt": prompt, "max_tokens": max_tokens}
        )
        if not self._responses:
            raise LLMProviderError("scripted provider exhausted")
        text = self._responses.pop(0)
        if isinstance(text, Exception):
            raise text
        return LLMResponse(
            text=text, provider=self.provider_id, model=self._model
        )


class DeterministicProvider:
    """Offline provider that always answers with contract-valid JSON.

    It derives a stable reply from the prompt itself, so runs are fully
    reproducible without network access or an API key.  It also serves as
    the semantic template for what a "well-formed" model answer looks like.
    """

    provider_id = "deterministic"

    def __init__(self, model: str = "deterministic-offline") -> None:
        self._model = model

    def complete(
        self,
        prompt: str,
        *,
        system: str = "",
        max_tokens: int = 1024,
        temperature: float = 0.2,
    ) -> LLMResponse:
        if '"decision"' in system or "decision" in system:
            decision = "approve" if "approve" in system else "endorse"
            payload = {
                "decision": decision,
                "rationale": "确定性离线评审：输出符合契约，未发现阻断性问题",
                "confidence": 0.8,
            }
        else:
            task = _extract_section(prompt, "任务")
            seed = hashlib.sha256(task.encode("utf-8")).hexdigest()[:8]
            payload = {
                "content": f"[确定性提案 {seed}]\n任务：{task}\n"
                "已按统一契约生成，内容与上游输入保持一致。",
                "confidence": 0.8,
                "actions": [],
                "artifacts": {},
            }
        return LLMResponse(
            text=json.dumps(payload, ensure_ascii=False),
            provider=self.provider_id,
            model=self._model,
        )


class CachedProvider:
    """Memoizes identical calls so re-runs and replays are cheap and stable.

    Two providers with different backends produce different latencies and
    costs; caching canonicalizes repeat traffic so a replayed topology gets
    the same answers without paying for them again.
    """

    provider_id = "cached"

    def __init__(self, inner: LLMProvider) -> None:
        self._inner = inner
        self._cache: dict[str, LLMResponse] = {}
        self.hits = 0

    @property
    def inner_provider_id(self) -> str:
        return getattr(self._inner, "provider_id", "unknown")

    def complete(
        self,
        prompt: str,
        *,
        system: str = "",
        max_tokens: int = 1024,
        temperature: float = 0.2,
    ) -> LLMResponse:
        inner_model = getattr(self._inner, "model", "") or getattr(
            self._inner, "_model", ""
        )
        key = "|".join(
            (
                str(getattr(self._inner, "provider_id", "")),
                str(inner_model),
                system,
                prompt,
                str(temperature),
                str(max_tokens),
            )
        )
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        if digest in self._cache:
            self.hits += 1
            return self._cache[digest]
        response = self._inner.complete(
            prompt, system=system, max_tokens=max_tokens, temperature=temperature
        )
        self._cache[digest] = response
        return response


class OpenAICompatProvider:
    """Stdlib-only client for OpenAI-compatible chat completion endpoints.

    Works with OpenAI, DeepSeek, Qwen (DashScope compatible mode), Moonshot,
    Ollama (``/v1``), vLLM, and other vendors exposing ``/chat/completions``.
    Transport errors and transient HTTP statuses are retried with backoff.
    """

    provider_id = "openai-compatible"

    def __init__(
        self,
        *,
        model: str,
        base_url: str = "https://api.deepseek.com/v1",
        api_key: str = "",
        timeout: float = 60.0,
        max_retries: int = 2,
        retry_backoff: float = 1.0,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_backoff = retry_backoff

    @staticmethod
    def build_payload(
        model: str,
        system: str,
        prompt: str,
        *,
        max_tokens: int,
        temperature: float,
    ) -> dict[str, Any]:
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        return {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }

    @staticmethod
    def build_headers(api_key: str) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return headers

    @staticmethod
    def parse_response(payload: Mapping[str, Any]) -> tuple[str, dict[str, int]]:
        choices = payload.get("choices") or []
        if not choices:
            raise LLMProviderError("response contains no choices")
        message = choices[0].get("message") or {}
        text = str(message.get("content") or "")
        usage_raw = payload.get("usage") or {}
        usage = {
            key: int(usage_raw[key])
            for key in ("prompt_tokens", "completion_tokens", "total_tokens")
            if key in usage_raw
        }
        return text, usage

    def complete(
        self,
        prompt: str,
        *,
        system: str = "",
        max_tokens: int = 1024,
        temperature: float = 0.2,
    ) -> LLMResponse:
        payload = self.build_payload(
            self.model,
            system,
            prompt,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = self.build_headers(self.api_key)
        url = f"{self.base_url}/chat/completions"

        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                request = urllib.request.Request(
                    url, data=body, headers=headers, method="POST"
                )
                with urllib.request.urlopen(request, timeout=self.timeout) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                text, usage = self.parse_response(data)
                return LLMResponse(
                    text=text,
                    provider=self.provider_id,
                    model=self.model,
                    usage=usage,
                    raw={"endpoint": url},
                )
            except urllib.error.HTTPError as exc:
                last_error = exc
                if exc.code not in (408, 409, 429, 500, 502, 503, 504):
                    break
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                last_error = exc
            if attempt < self.max_retries:
                time.sleep(self.retry_backoff * (2**attempt))
        raise LLMProviderError(
            f"provider call failed after {self.max_retries + 1} attempt(s): {last_error}"
        )


def provider_from_env(env: Mapping[str, str] | None = None) -> LLMProvider:
    """Build a provider from environment variables.

    ``NARTHEX_LLM_MODEL`` unset -> :class:`DeterministicProvider` (offline).
    Otherwise an :class:`OpenAICompatProvider` is configured from
    ``NARTHEX_LLM_MODEL``, ``NARTHEX_LLM_BASE_URL`` (default DeepSeek),
    ``NARTHEX_LLM_API_KEY``.  This lets users switch models without touching
    topology files or agent code.
    """
    source = dict(os.environ if env is None else env)
    model = source.get("NARTHEX_LLM_MODEL", "").strip()
    if not model:
        return DeterministicProvider()
    return OpenAICompatProvider(
        model=model,
        base_url=source.get("NARTHEX_LLM_BASE_URL", "https://api.deepseek.com/v1"),
        api_key=source.get("NARTHEX_LLM_API_KEY", ""),
    )


_SECTION_RE = re.compile(r"^##\s*(?P<title>.+?)\s*$", re.MULTILINE)


def _extract_section(text: str, title: str) -> str:
    matches = list(_SECTION_RE.finditer(text))
    for index, match in enumerate(matches):
        if match.group("title").strip() == title:
            end = (
                matches[index + 1].start()
                if index + 1 < len(matches)
                else len(text)
            )
            return text[match.end() : end].strip()
    return text.strip()
