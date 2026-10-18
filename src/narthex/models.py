"""Core declarative models for Narthex topologies.

The models are intentionally dependency free so that a topology can be
validated and inspected without importing a runtime, an LLM SDK, or a web
framework.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping


class Mode(str, Enum):
    """Governance mode for a runtime node."""

    CATHEDRAL = "cathedral"
    BAZAAR = "bazaar"

    @classmethod
    def parse(cls, value: "Mode | str | None", default: "Mode | None" = None) -> "Mode":
        if value is None:
            if default is None:
                raise ValueError("mode is required")
            return default
        if isinstance(value, cls):
            return value
        normalized = str(value).strip().lower()
        try:
            return cls(normalized)
        except ValueError as exc:
            raise ValueError(f"unknown governance mode: {value!r}") from exc


class EdgeType(str, Enum):
    """Semantics of a topology edge."""

    CONSULT = "consult"
    AUTHORIZE = "authorize"

    @classmethod
    def parse(cls, value: "EdgeType | str") -> "EdgeType":
        if isinstance(value, cls):
            return value
        try:
            return cls(str(value).strip().lower())
        except ValueError as exc:
            raise ValueError(f"unknown edge type: {value!r}") from exc


def _string_set(value: Any) -> frozenset[str]:
    if value is None:
        return frozenset()
    if isinstance(value, str):
        return frozenset({value})
    return frozenset(str(item) for item in value)


def _string_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(str(item) for item in value)


@dataclass(frozen=True)
class AgentSpec:
    """Static identity and maximum capability envelope of an agent."""

    id: str
    role: str
    capabilities: frozenset[str] = frozenset()
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, agent_id: str, data: Mapping[str, Any] | None) -> "AgentSpec":
        data = dict(data or {})
        return cls(
            id=str(agent_id),
            role=str(data.get("role", agent_id)),
            capabilities=_string_set(data.get("capabilities")),
            metadata=dict(data.get("metadata") or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "capabilities": sorted(self.capabilities),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class ApprovalSpec:
    """Cathedral gate configuration for a node."""

    required: bool = True
    approvers: tuple[str, ...] = ()
    scope: frozenset[str] = frozenset()

    @classmethod
    def from_dict(
        cls,
        data: Mapping[str, Any] | None,
        *,
        fallback_scope: Any = None,
    ) -> "ApprovalSpec":
        data = dict(data or {})
        scope = data.get("scope", fallback_scope)
        return cls(
            required=bool(data.get("required", True)),
            approvers=_string_tuple(data.get("approvers")),
            scope=_string_set(scope),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "required": self.required,
            "approvers": list(self.approvers),
            "scope": sorted(self.scope),
        }


@dataclass(frozen=True)
class NodeSpec:
    """A node in the declarative orchestration graph."""

    id: str
    agent: str
    phase: str = "work"
    risk: frozenset[str] = frozenset()
    task: str = ""
    capabilities: frozenset[str] | None = None
    mode: Mode | None = None
    approval: ApprovalSpec = field(default_factory=ApprovalSpec)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "NodeSpec":
        data = dict(data)
        node_id = str(data.get("id", "")).strip()
        if not node_id:
            raise ValueError("node id is required")
        agent = str(data.get("agent", "")).strip()
        if not agent:
            raise ValueError(f"node {node_id!r} has no agent")
        raw_caps = data.get("capabilities")
        capabilities = None if raw_caps is None else _string_set(raw_caps)
        mode_value = data.get("mode")
        mode = None if mode_value in (None, "") else Mode.parse(mode_value)
        approval_data = data.get("approval")
        if approval_data is None:
            approval_data = {}
        elif not isinstance(approval_data, Mapping):
            raise ValueError(f"node {node_id!r} approval must be a mapping")
        approval = ApprovalSpec.from_dict(
            approval_data,
            fallback_scope=data.get("approval_scope"),
        )
        return cls(
            id=node_id,
            agent=agent,
            phase=str(data.get("phase", "work")),
            risk=_string_set(data.get("risk")),
            task=str(data.get("task", "")),
            capabilities=capabilities,
            mode=mode,
            approval=approval,
            metadata=dict(data.get("metadata") or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": self.id,
            "agent": self.agent,
            "phase": self.phase,
            "risk": sorted(self.risk),
            "task": self.task,
            "approval": self.approval.to_dict(),
            "metadata": dict(self.metadata),
        }
        if self.capabilities is not None:
            result["capabilities"] = sorted(self.capabilities)
        if self.mode is not None:
            result["mode"] = self.mode.value
        return result


@dataclass(frozen=True)
class EdgeSpec:
    """A directed execution edge with governance semantics."""

    source: str
    target: str
    type: EdgeType = EdgeType.CONSULT

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EdgeSpec":
        source = str(data.get("from", data.get("source", ""))).strip()
        target = str(data.get("to", data.get("target", ""))).strip()
        if not source or not target:
            raise ValueError("edge requires 'from' and 'to'")
        return cls(
            source=source,
            target=target,
            type=EdgeType.parse(data.get("type", "consult")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {"from": self.source, "to": self.target, "type": self.type.value}


@dataclass(frozen=True)
class GovernorConfig:
    """Thresholds used by the Topology Governor."""

    high_risk_tags: tuple[str, ...] = (
        "risk:high",
        "risk:critical",
        "risk:safety",
    )
    exploratory_tags: tuple[str, ...] = ("risk:exploratory", "risk:low")
    commit_phases: tuple[str, ...] = (
        "commit",
        "production",
        "deploy",
        "release",
        "execute",
    )
    token_budget: int = 4000
    divergence_threshold: float = 0.75
    max_rounds: int = 3
    consensus_threshold: float = 0.6

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> "GovernorConfig":
        data = dict(data or {})
        return cls(
            high_risk_tags=_string_tuple(data.get("high_risk_tags")) or cls.high_risk_tags,
            exploratory_tags=_string_tuple(data.get("exploratory_tags")) or cls.exploratory_tags,
            commit_phases=_string_tuple(data.get("commit_phases")) or cls.commit_phases,
            token_budget=int(data.get("token_budget", cls.token_budget)),
            divergence_threshold=float(
                data.get("divergence_threshold", cls.divergence_threshold)
            ),
            max_rounds=int(data.get("max_rounds", cls.max_rounds)),
            consensus_threshold=float(
                data.get("consensus_threshold", cls.consensus_threshold)
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "high_risk_tags": list(self.high_risk_tags),
            "exploratory_tags": list(self.exploratory_tags),
            "commit_phases": list(self.commit_phases),
            "token_budget": self.token_budget,
            "divergence_threshold": self.divergence_threshold,
            "max_rounds": self.max_rounds,
            "consensus_threshold": self.consensus_threshold,
        }


@dataclass
class Topology:
    """A validated directed graph of agents, nodes, and governance edges."""

    name: str
    description: str = ""
    default_mode: Mode = Mode.BAZAAR
    governor: GovernorConfig = field(default_factory=GovernorConfig)
    agents: dict[str, AgentSpec] = field(default_factory=dict)
    nodes: dict[str, NodeSpec] = field(default_factory=dict)
    edges: tuple[EdgeSpec, ...] = ()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Topology":
        if not isinstance(data, Mapping):
            raise ValueError("topology root must be a mapping")
        name = str(data.get("name", "")).strip()
        if not name:
            raise ValueError("topology name is required")

        agents: dict[str, AgentSpec] = {}
        raw_agents = data.get("agents") or {}
        if isinstance(raw_agents, Mapping):
            iterable: Iterable[tuple[str, Any]] = raw_agents.items()
        elif isinstance(raw_agents, list):
            iterable = []
            for item in raw_agents:
                if not isinstance(item, Mapping) or not item.get("id"):
                    raise ValueError("agent list entries require an id")
                iterable.append((str(item["id"]), item))
        else:
            raise ValueError("agents must be a mapping or list")

        for agent_id, agent_data in iterable:
            spec = AgentSpec.from_dict(str(agent_id), agent_data)
            if spec.id in agents:
                raise ValueError(f"duplicate agent id: {spec.id!r}")
            agents[spec.id] = spec

        nodes: dict[str, NodeSpec] = {}
        raw_nodes = data.get("nodes") or []
        if isinstance(raw_nodes, Mapping):
            node_entries = []
            for node_id, node_data in raw_nodes.items():
                node_data = dict(node_data or {})
                node_data.setdefault("id", node_id)
                node_entries.append(node_data)
            raw_nodes = node_entries
        if not isinstance(raw_nodes, list):
            raise ValueError("nodes must be a list or mapping")
        for node_data in raw_nodes:
            if not isinstance(node_data, Mapping):
                raise ValueError("each node must be a mapping")
            node = NodeSpec.from_dict(node_data)
            if node.id in nodes:
                raise ValueError(f"duplicate node id: {node.id!r}")
            nodes[node.id] = node

        edges: list[EdgeSpec] = []
        for edge_data in data.get("edges") or []:
            if not isinstance(edge_data, Mapping):
                raise ValueError("each edge must be a mapping")
            edges.append(EdgeSpec.from_dict(edge_data))

        topology = cls(
            name=name,
            description=str(data.get("description", "")),
            default_mode=Mode.parse(data.get("default_mode", "bazaar")),
            governor=GovernorConfig.from_dict(data.get("governor")),
            agents=agents,
            nodes=nodes,
            edges=tuple(edges),
        )
        topology.validate()
        return topology

    def validate(self) -> None:
        from .errors import TopologyError

        if not self.nodes:
            raise TopologyError("topology must contain at least one node")
        if not self.agents:
            raise TopologyError("topology must declare at least one agent")

        for node in self.nodes.values():
            if node.agent not in self.agents:
                raise TopologyError(
                    f"node {node.id!r} references unknown agent {node.agent!r}"
                )
            if node.capabilities is not None:
                from .capabilities import capability_grants

                agent_caps = self.agents[node.agent].capabilities
                missing = {
                    capability
                    for capability in node.capabilities
                    if not capability_grants(agent_caps, capability)
                }
                if missing:
                    raise TopologyError(
                        f"node {node.id!r} requests capabilities not held by agent "
                        f"{node.agent!r}: {sorted(missing)}"
                    )
            for approver in node.approval.approvers:
                if approver not in self.agents:
                    raise TopologyError(
                        f"node {node.id!r} references unknown approver {approver!r}"
                    )
            if node.mode is Mode.BAZAAR and (
                set(node.risk) & set(self.governor.high_risk_tags)
                or node.phase in self.governor.commit_phases
            ):
                # Allowed: the Governor will override this unsafe request and
                # record the policy override in the audit trail.
                pass

        seen: set[tuple[str, str, str]] = set()
        for edge in self.edges:
            if edge.source not in self.nodes:
                raise TopologyError(f"edge source not found: {edge.source!r}")
            if edge.target not in self.nodes:
                raise TopologyError(f"edge target not found: {edge.target!r}")
            if edge.source == edge.target:
                raise TopologyError(f"self edge on node {edge.source!r}")
            key = (edge.source, edge.target, edge.type.value)
            if key in seen:
                raise TopologyError(
                    f"duplicate edge {edge.source!r}->{edge.target!r} ({edge.type.value})"
                )
            seen.add(key)

        # Kahn's algorithm both validates acyclicity and gives a stable order.
        self.order()

    def incoming(self, node_id: str) -> tuple[EdgeSpec, ...]:
        return tuple(edge for edge in self.edges if edge.target == node_id)

    def outgoing(self, node_id: str) -> tuple[EdgeSpec, ...]:
        return tuple(edge for edge in self.edges if edge.source == node_id)

    def order(self) -> tuple[str, ...]:
        """Return a stable topological order, raising on cycles."""
        from .errors import TopologyError

        order_index = {node_id: index for index, node_id in enumerate(self.nodes)}
        indegree = {node_id: 0 for node_id in self.nodes}
        adjacency: dict[str, list[str]] = {node_id: [] for node_id in self.nodes}
        for edge in self.edges:
            if edge.source not in indegree or edge.target not in indegree:
                raise TopologyError("cannot order a topology with unknown nodes")
            adjacency[edge.source].append(edge.target)
            indegree[edge.target] += 1

        import heapq

        ready = [
            (order_index[node_id], node_id)
            for node_id, degree in indegree.items()
            if degree == 0
        ]
        heapq.heapify(ready)
        ordered: list[str] = []
        while ready:
            _, node_id = heapq.heappop(ready)
            ordered.append(node_id)
            for target in adjacency[node_id]:
                indegree[target] -= 1
                if indegree[target] == 0:
                    heapq.heappush(ready, (order_index[target], target))

        if len(ordered) != len(self.nodes):
            cyclic = sorted(node_id for node_id, degree in indegree.items() if degree > 0)
            raise TopologyError(f"topology contains a cycle involving: {cyclic}")
        return tuple(ordered)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "default_mode": self.default_mode.value,
            "governor": self.governor.to_dict(),
            "agents": {agent_id: spec.to_dict() for agent_id, spec in self.agents.items()},
            "nodes": [node.to_dict() for node in self.nodes.values()],
            "edges": [edge.to_dict() for edge in self.edges],
        }
