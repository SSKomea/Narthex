"""LLM-backed agent with bounded repair rounds and deterministic fallback.

The agent never lets a model failure change governance semantics:

1. Same prompt contract for every backend.
2. One repair round when the answer breaks the contract.
3. Deterministic :class:`DemoAgent` fallback when the model still fails,
   so a run always produces valid ``AgentOutput`` / ``Review`` values.
4. Token accounting always uses the model-agnostic ``estimate_tokens``,
   so Governor budgets behave identically across providers.
"""

from __future__ import annotations

from typing import Any

from ..models import AgentSpec, Topology
from ..runtime.agents import (
    AgentContext,
    AgentOutput,
    BaseAgent,
    DemoAgent,
    Review,
    ReviewContext,
    estimate_tokens,
)
from .contracts import (
    PROPOSE_SYSTEM,
    ProposeContract,
    ReviewContract,
    build_propose_prompt,
    build_review_prompt,
    build_revise_prompt,
)
from .provider import CachedProvider, LLMProvider, LLMProviderError, provider_from_env


class LLMAgent(BaseAgent):
    """Drop-in replacement for :class:`DemoAgent` backed by any provider."""

    def __init__(
        self,
        spec: AgentSpec,
        provider: LLMProvider,
        *,
        propose_contract: ProposeContract | None = None,
        review_contract: ReviewContract | None = None,
        temperature: float = 0.2,
        max_tokens: int = 1024,
        repair_rounds: int = 1,
    ) -> None:
        super().__init__(spec)
        self.provider = provider
        self.propose_contract = propose_contract or ProposeContract()
        self.review_contract = review_contract or ReviewContract()
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.repair_rounds = repair_rounds
        self._fallback = DemoAgent(spec)

    # ------------------------------------------------------------------
    def _call(self, system: str, prompt: str) -> str:
        response = self.provider.complete(
            prompt,
            system=system,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
        )
        return response.text

    def _model_meta(self) -> dict[str, Any]:
        return {
            "provider": getattr(self.provider, "provider_id", "unknown"),
            "model": getattr(self.provider, "model", "")
            or getattr(self.provider, "_model", "unknown"),
        }

    # ------------------------------------------------------------------
    def propose(self, context: AgentContext) -> AgentOutput:
        system = self.propose_contract.system
        prompt = build_propose_prompt(context)
        fallback_content = self._fallback.propose(context).content
        last_error: Exception | None = None
        for attempt in range(self.repair_rounds + 1):
            try:
                text = self._call(system, prompt)
                output = self.propose_contract.parse(
                    text, context, fallback_content=""
                )
            except (ValueError, LLMProviderError) as exc:
                last_error = exc
                if attempt < self.repair_rounds:
                    prompt = (
                        prompt
                        + "\n\n你上一次的回答不符合契约（"
                        + str(exc)
                        + "）。请严格只输出一个符合字段要求的 JSON 对象。"
                    )
                continue
            output.metadata.update(self._model_meta())
            output.metadata["llm_repair_rounds"] = attempt
            # Token cost is recomputed model-agnostically so the Governor's
            # budget signal does not depend on vendor usage reports.
            output.token_cost = estimate_tokens(output.content)
            return output
        # Deterministic fallback keeps the run valid instead of failing.
        return AgentOutput(
            content=fallback_content,
            confidence=0.8,
            metadata={
                **self._model_meta(),
                "llm_fallback": True,
                "llm_error": f"{type(last_error).__name__}: {last_error}",
            },
        )

    def review(self, context: ReviewContext) -> Review:
        system = self.review_contract.system_for(context.mode)
        prompt = build_review_prompt(context)
        try:
            text = self._call(system, prompt)
            return self.review_contract.parse(text, context.mode)
        except LLMProviderError:
            fallback = self._fallback.review(context)
            return Review(
                decision=fallback.decision,
                rationale=fallback.rationale + "（LLM 不可用，确定性兜底）",
                confidence=fallback.confidence,
                token_cost=fallback.token_cost,
            )

    def revise(
        self,
        context: AgentContext,
        output: AgentOutput,
        reviews: list[Review],
    ) -> AgentOutput:
        prompt = build_revise_prompt(context, output, reviews)
        fallback_content = self._fallback.revise(context, output, reviews).content
        for attempt in range(self.repair_rounds + 1):
            try:
                text = self._call(PROPOSE_SYSTEM, prompt)
                revised = self.propose_contract.parse(
                    text, context, fallback_content=""
                )
            except (ValueError, LLMProviderError):
                if attempt >= self.repair_rounds:
                    return AgentOutput(
                        content=fallback_content,
                        confidence=0.8,
                        metadata={
                            **self._model_meta(),
                            "llm_fallback": True,
                            "revised": True,
                        },
                    )
                prompt += "\n\n你上一次的回答不符合契约。请严格只输出一个 JSON 对象。"
                continue
            revised.metadata.update(self._model_meta())
            revised.metadata["revised"] = True
            revised.metadata["llm_repair_rounds"] = attempt
            revised.token_cost = estimate_tokens(revised.content)
            return revised
        return output


def build_llm_registry(
    topology: Topology,
    provider: LLMProvider | None = None,
    *,
    cache: bool = True,
    **agent_kwargs: Any,
):
    """Wrap every agent declared in the topology into an :class:`LLMAgent`.

    ``provider`` defaults to :func:`provider_from_env`, so switching backends
    is an environment change rather than a code change.
    """
    from ..runtime.agents import AgentRegistry

    if provider is None:
        provider = provider_from_env()
    if cache and not isinstance(provider, CachedProvider):
        provider = CachedProvider(provider)
    registry = AgentRegistry()
    for spec in topology.agents.values():
        registry.register(LLMAgent(spec, provider, **agent_kwargs))
    return registry
