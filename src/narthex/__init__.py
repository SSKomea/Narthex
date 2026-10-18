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
from .runtime.approval import AutoApprovalProvider, RecordingApprovalProvider
from .runtime.engine import NarthexRuntime, RuntimeOptions
from .runtime.snapshot import Snapshot
from .runtime.state import NodeResult, RunResult
from .runtime.tools import Tool, ToolAction, ToolRegistry, build_demo_tools
from .topology.dsl import TopologyBuilder, governor
from .topology.loader import load_topology, loads_topology

__version__ = "0.1.0"

__all__ = [
    "AgentContext",
    "AgentOutput",
    "AgentRegistry",
    "AgentSpec",
    "ApprovalSpec",
    "AutoApprovalProvider",
    "BaseAgent",
    "EdgeSpec",
    "EdgeType",
    "FunctionAgent",
    "GovernorConfig",
    "Mode",
    "NarthexRuntime",
    "NodeResult",
    "NodeSpec",
    "RecordingApprovalProvider",
    "Review",
    "ReviewContext",
    "ReviewDecision",
    "RunResult",
    "RuntimeOptions",
    "Snapshot",
    "Tool",
    "ToolAction",
    "ToolRegistry",
    "Topology",
    "TopologyBuilder",
    "build_demo_registry",
    "build_demo_tools",
    "governor",
    "load_topology",
    "loads_topology",
]
