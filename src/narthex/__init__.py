"""Narthex: a runtime-switchable Cathedral/Bazaar multi-agent orchestrator."""

from .models import (
    AgentSpec,
    ApprovalSpec,
    EdgeSpec,
    EdgeType,
    GovernorConfig,
    Mode,
    NodeSpec,
    Topology,
)
from .runtime.agents import (
    AgentContext,
    AgentOutput,
    AgentRegistry,
    BaseAgent,
    FunctionAgent,
    Review,
    ReviewContext,
    ReviewDecision,
    build_demo_registry,
)
from .llm import (
    CachedProvider,
    DeterministicProvider,
    LLMAgent,
    LLMProvider,
    LLMResponse,
    OpenAICompatProvider,
    ProposeContract,
    ReviewContract,
    ScriptedProvider,
    build_llm_registry,
    normalize_confidence,
    normalize_decision,
    provider_from_env,
)
from .runtime.approval import AutoApprovalProvider, RecordingApprovalProvider
from .runtime.engine import NarthexRuntime, RuntimeOptions
from .runtime.snapshot import Snapshot
from .runtime.state import NodeResult, RunResult
from .runtime.tools import Tool, ToolAction, ToolRegistry, build_demo_tools
from .topology.dsl import TopologyBuilder, governor
from .topology.loader import load_topology, loads_topology

__version__ = "0.2.0"

__all__ = [
    "AgentContext",
    "AgentOutput",
    "AgentRegistry",
    "AgentSpec",
    "ApprovalSpec",
    "AutoApprovalProvider",
    "BaseAgent",
    "CachedProvider",
    "DeterministicProvider",
    "EdgeSpec",
    "EdgeType",
    "FunctionAgent",
    "GovernorConfig",
    "LLMAgent",
    "LLMProvider",
    "LLMResponse",
    "Mode",
    "NarthexRuntime",
    "NodeResult",
    "NodeSpec",
    "OpenAICompatProvider",
    "ProposeContract",
    "RecordingApprovalProvider",
    "Review",
    "ReviewContract",
    "ReviewContext",
    "ReviewDecision",
    "RunResult",
    "RuntimeOptions",
    "ScriptedProvider",
    "Snapshot",
    "Tool",
    "ToolAction",
    "ToolRegistry",
    "Topology",
    "TopologyBuilder",
    "build_demo_registry",
    "build_demo_tools",
    "build_llm_registry",
    "governor",
    "load_topology",
    "loads_topology",
    "normalize_confidence",
    "normalize_decision",
    "provider_from_env",
]
