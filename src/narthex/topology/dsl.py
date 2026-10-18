"""Python DSL for defining Narthex topologies.

Example::

    topology = (
        TopologyBuilder("demo", default_mode="bazaar")
        .agent("planner", role="planner", capabilities=["propose"])
        .node("brief", agent="planner", phase="explore",
              risk=["risk:exploratory"])
        .build()
    )
"""

from __future__ import annotations

from typing import Any, Iterable

from ..models import (
    AgentSpec,
    ApprovalSpec,
    EdgeSpec,
    EdgeType,
    GovernorConfig,
    Mode,
    NodeSpec,
    Topology,
)


def governor(**kwargs: Any) -> GovernorConfig:
    """Create a GovernorConfig from keyword arguments."""
    known = {
        "high_risk_tags",
        "exploratory_tags",
        "commit_phases",
        "token_budget",
        "divergence_threshold",
        "max_rounds",
        "consensus_threshold",
    }
    unknown = set(kwargs) - known
    if unknown:
        raise ValueError(f"unknown governor options: {sorted(unknown)}")
    return GovernorConfig.from_dict(kwargs)


class TopologyBuilder:
    """Fluent builder that keeps definition order stable."""

    def __init__(
        self,
        name: str,
        *,
        description: str = "",
        default_mode: Mode | str = Mode.BAZAAR,
        governor: GovernorConfig | None = None,
    ) -> None:
        self._name = name
        self._description = description
        self._default_mode = Mode.parse(default_mode)
        self._governor = governor or GovernorConfig()
        self._agents: dict[str, AgentSpec] = {}
        self._nodes: dict[str, NodeSpec] = {}
        self._edges: list[EdgeSpec] = []

    def agent(
        self,
        agent_id: str,
        *,
        role: str | None = None,
        capabilities: Iterable[str] = (),
        metadata: dict[str, Any] | None = None,
    ) -> "TopologyBuilder":
        spec = AgentSpec(
            id=agent_id,
            role=role or agent_id,
            capabilities=frozenset(capabilities),
            metadata=dict(metadata or {}),
        )
        if agent_id in self._agents:
            raise ValueError(f"duplicate agent id: {agent_id!r}")
        self._agents[agent_id] = spec
        return self

    def node(
        self,
        node_id: str,
        *,
        agent: str,
        phase: str = "work",
        risk: Iterable[str] = (),
        task: str = "",
        capabilities: Iterable[str] | None = None,
        mode: Mode | str | None = None,
        approvers: Iterable[str] = (),
        approval_scope: Iterable[str] | None = None,
        approval_required: bool = True,
        metadata: dict[str, Any] | None = None,
    ) -> "TopologyBuilder":
        spec = NodeSpec(
            id=node_id,
            agent=agent,
            phase=phase,
            risk=frozenset(risk),
            task=task,
            capabilities=None if capabilities is None else frozenset(capabilities),
            mode=None if mode is None else Mode.parse(mode),
            approval=ApprovalSpec(
                required=approval_required,
                approvers=tuple(approvers),
                scope=frozenset(approval_scope or ()),
            ),
            metadata=dict(metadata or {}),
        )
        if node_id in self._nodes:
            raise ValueError(f"duplicate node id: {node_id!r}")
        self._nodes[node_id] = spec
        return self

    def edge(
        self,
        source: str,
        target: str,
        *,
        type: EdgeType | str = EdgeType.CONSULT,
    ) -> "TopologyBuilder":
        self._edges.append(
            EdgeSpec(source=source, target=target, type=EdgeType.parse(type))
        )
        return self

    def consult(self, source: str, target: str) -> "TopologyBuilder":
        return self.edge(source, target, type=EdgeType.CONSULT)

    def authorize(self, source: str, target: str) -> "TopologyBuilder":
        return self.edge(source, target, type=EdgeType.AUTHORIZE)

    def build(self) -> Topology:
        topology = Topology(
            name=self._name,
            description=self._description,
            default_mode=self._default_mode,
            governor=self._governor,
            agents=dict(self._agents),
            nodes=dict(self._nodes),
            edges=tuple(self._edges),
        )
        topology.validate()
        return topology


# Functional helpers make short DSL modules easier to write.
def agent(agent_id: str, **kwargs: Any) -> AgentSpec:
    builder = TopologyBuilder("_scratch").agent(agent_id, **kwargs)
    return builder._agents[agent_id]


def node(node_id: str, **kwargs: Any) -> NodeSpec:
    builder = TopologyBuilder("_scratch").node(node_id, **kwargs)
    return builder._nodes[node_id]


def edge(
    source: str, target: str, *, type: EdgeType | str = EdgeType.CONSULT
) -> EdgeSpec:
    return EdgeSpec(source=source, target=target, type=EdgeType.parse(type))
