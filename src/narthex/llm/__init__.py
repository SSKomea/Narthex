"""LLM adapter layer for model-agnostic agent behavior."""

from .agent import LLMAgent, build_llm_registry
from .contracts import ProposeContract, ReviewContract
from .normalize import normalize_confidence, normalize_decision
from .provider import (
    CachedProvider,
    DeterministicProvider,
    LLMProvider,
    LLMResponse,
    OpenAICompatProvider,
    ScriptedProvider,
    provider_from_env,
)

__all__ = [
    "CachedProvider",
    "DeterministicProvider",
    "LLMAgent",
    "LLMProvider",
    "LLMResponse",
    "OpenAICompatProvider",
    "ProposeContract",
    "ReviewContract",
    "ScriptedProvider",
    "build_llm_registry",
    "normalize_confidence",
    "normalize_decision",
    "provider_from_env",
]
