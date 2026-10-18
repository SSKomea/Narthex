"""Small deterministic consensus metrics used by Bazaar nodes.

These are intentionally model-agnostic.  A caller can replace them with an
embedding distance or an LLM judge without changing the Governor interface.
"""

from __future__ import annotations

import math
import re
from typing import Iterable

_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]")


def tokenize(text: str) -> set[str]:
    return {match.group(0).lower() for match in _TOKEN_RE.finditer(text)}


def jaccard_distance(left: str, right: str) -> float:
    left_tokens = tokenize(left)
    right_tokens = tokenize(right)
    if not left_tokens and not right_tokens:
        return 0.0
    union = left_tokens | right_tokens
    if not union:
        return 0.0
    return 1.0 - len(left_tokens & right_tokens) / len(union)


def text_divergence(texts: Iterable[str]) -> float:
    """Return the maximum pairwise Jaccard distance among non-empty texts."""
    values = [text for text in texts if text and text.strip()]
    if len(values) < 2:
        return 0.0
    maximum = 0.0
    for index, left in enumerate(values):
        for right in values[index + 1 :]:
            maximum = max(maximum, jaccard_distance(left, right))
    return maximum


def normalize_score(value: float) -> float:
    if math.isnan(value) or math.isinf(value):
        return 0.0
    return min(1.0, max(0.0, value))
