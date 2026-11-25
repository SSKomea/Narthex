"""Output normalization: canonical decisions, confidence, and JSON extraction.

Different models phrase the same verdict differently ("approve", "通过",
"LGTM", "同意批准").  Everything is mapped onto the fixed five-decision
vocabulary before it reaches the Governor, so governance semantics do not
drift with the backend.
"""

from __future__ import annotations

import json
import re
from typing import Any, Mapping

_DECISION_SYNONYMS: dict[str, tuple[str, ...]] = {
    "endorse": (
        "endorse",
        "endorsed",
        "support",
        "supported",
        "agree",
        "agreed",
        "accept",
        "accepted",
        "acknowledge",
        "认可",
        "同意",
        "支持",
        "赞同",
        "赞成",
        "无异议",
        "同意提案",
    ),
    "challenge": (
        "challenge",
        "challenged",
        "revise",
        "revision_required",
        "changes_requested",
        "request_changes",
        "object",
        "objection",
        "concern",
        "质疑",
        "异议",
        "反对",
        "需修改",
        "需要修改",
        "不认可",
    ),
    "approve": (
        "approve",
        "approved",
        "pass",
        "passed",
        "lgtm",
        "ok",
        "sign_off",
        "signoff",
        "通过",
        "批准",
        "同意批准",
        "同意执行",
        "审核通过",
        "审查通过",
    ),
    "deny": (
        "deny",
        "denied",
        "reject",
        "rejected",
        "veto",
        "blocked",
        "拒绝",
        "否决",
        "驳回",
        "不同意",
        "不予批准",
        "禁止",
    ),
    "escalate": (
        "escalate",
        "escalated",
        "human_review",
        "needs_human",
        "manual_review",
        "uncertain",
        "unknown",
        "升级",
        "上报",
        "人工审批",
        "人工评审",
        "人工介入",
        "无法判断",
        "不确定",
    ),
}

_SYNONYM_LOOKUP: dict[str, str] = {
    synonym: canonical
    for canonical, synonyms in _DECISION_SYNONYMS.items()
    for synonym in synonyms
}

_CONFIDENCE_LEVELS = {
    "high": 0.9,
    "medium": 0.7,
    "mid": 0.7,
    "low": 0.4,
    "very_high": 0.95,
    "very_low": 0.2,
    "高": 0.9,
    "中": 0.7,
    "低": 0.4,
}

_FENCE_RE = re.compile(
    r"```[a-zA-Z0-9_-]*\s*(?P<body>\{.*?\})\s*```", re.DOTALL
)


def normalize_decision(value: Any, *, allowed: tuple[str, ...]) -> str:
    """Map any phrasing of a verdict onto the canonical vocabulary.

    Exact enum values win first, then bilingual synonyms, then substring
    matching.  If nothing matches, the conservative default for the current
    governance channel is returned (``escalate`` / ``challenge``), so an
    unparsable answer can never silently widen permissions.
    """
    text = str(value or "").strip().lower().replace(" ", "_")
    if not text:
        return allowed[-1]
    if text in allowed:
        return text
    if text in _SYNONYM_LOOKUP:
        candidate = _SYNONYM_LOOKUP[text]
        if candidate in allowed:
            return candidate
    for canonical in allowed:
        if canonical in text:
            return canonical
    for synonym, candidate in _SYNONYM_LOOKUP.items():
        if synonym in text and candidate in allowed:
            return candidate
    return allowed[-1]


def normalize_confidence(value: Any) -> float:
    """Clamp arbitrary model confidence into [0, 1].

    Handles strings ("high"), 0-100 scales, 0-10 scales, and numeric noise.
    """
    if value is None:
        return 0.8
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in _CONFIDENCE_LEVELS:
            return _CONFIDENCE_LEVELS[lowered]
        try:
            value: float = float(lowered)
        except ValueError:
            return 0.8
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.8
    if number != number or number in (float("inf"), float("-inf")):
        return 0.8
    while number > 1.0:
        if number <= 10.0:
            number /= 10.0
        elif number <= 100.0:
            number /= 100.0
        else:
            number /= 100.0
    return min(1.0, max(0.0, number))


def extract_json_object(text: str) -> dict[str, Any] | None:
    """Extract the first JSON object from arbitrary model output.

    Tolerates markdown fences, prose prefixes/suffixes, and full-width
    quotes that some CJK-trained models emit.
    """
    if not text:
        return None
    candidates: list[str] = []
    fenced = _FENCE_RE.search(text)
    if fenced:
        candidates.append(fenced.group("body"))
    stripped = text.strip()
    candidates.append(stripped)
    first = stripped.find("{")
    last = stripped.rfind("}")
    if first != -1 and last > first:
        candidates.append(stripped[first : last + 1])
    for candidate in candidates:
        for variant in (candidate, candidate.replace("“", '"').replace("”", '"')):
            try:
                parsed = json.loads(variant)
            except (json.JSONDecodeError, ValueError):
                continue
            if isinstance(parsed, Mapping):
                return dict(parsed)
    return None


def coerce_actions(raw: Any) -> list[dict[str, Any]]:
    """Keep only well-formed tool actions; the capability boundary does the
    final authorization, but malformed entries are dropped here."""
    if not isinstance(raw, list):
        return []
    actions: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, Mapping) and str(item.get("tool", "")).strip():
            actions.append(
                {
                    "tool": str(item["tool"]).strip(),
                    "args": dict(item.get("args") or {}),
                    "reason": str(item.get("reason") or ""),
                }
            )
    return actions
