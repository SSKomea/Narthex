"""Serializable run state and result models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..events import to_jsonable


@dataclass
class NodeResult:
    node_id: str
    agent_id: str
    mode: str
    status: str
    output: str
    confidence: float
    token_cost: int
    capabilities: list[str] = field(default_factory=list)
    reviews: list[dict[str, Any]] = field(default_factory=list)
    actions: list[dict[str, Any]] = field(default_factory=list)
    tools: list[dict[str, Any]] = field(default_factory=list)
    decision_reasons: list[str] = field(default_factory=list)
    divergence: float = 0.0
    error: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_view(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "agent_id": self.agent_id,
            "mode": self.mode,
            "status": self.status,
            "output": self.output,
            "confidence": self.confidence,
            "token_cost": self.token_cost,
            "decision_reasons": list(self.decision_reasons),
            "error": self.error,
        }

    def to_dict(self) -> dict[str, Any]:
        return to_jsonable(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "NodeResult":
        return cls(**data)


@dataclass
class RunState:
    run_id: str
    task: str
    status: str = "pending"
    completed: list[str] = field(default_factory=list)
    results: dict[str, NodeResult] = field(default_factory=dict)
    total_tokens: int = 0
    max_divergence: float = 0.0
    last_mode: str | None = None
    branch_id: str | None = None
    parent_run_id: str | None = None
    replay_from: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "task": self.task,
            "status": self.status,
            "completed": list(self.completed),
            "results": {
                node_id: result.to_dict()
                for node_id, result in self.results.items()
            },
            "total_tokens": self.total_tokens,
            "max_divergence": self.max_divergence,
            "last_mode": self.last_mode,
            "branch_id": self.branch_id,
            "parent_run_id": self.parent_run_id,
            "replay_from": self.replay_from,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RunState":
        raw_results = data.get("results") or {}
        results = {
            str(node_id): NodeResult.from_dict(result)
            for node_id, result in raw_results.items()
        }
        return cls(
            run_id=str(data["run_id"]),
            task=str(data.get("task", "")),
            status=str(data.get("status", "pending")),
            completed=[str(node_id) for node_id in data.get("completed", [])],
            results=results,
            total_tokens=int(data.get("total_tokens", 0)),
            max_divergence=float(data.get("max_divergence", 0.0)),
            last_mode=data.get("last_mode"),
            branch_id=data.get("branch_id"),
            parent_run_id=data.get("parent_run_id"),
            replay_from=data.get("replay_from"),
            error=data.get("error"),
        )


@dataclass
class RunResult:
    run_id: str
    status: str
    task: str
    results: dict[str, NodeResult]
    audit_path: str
    run_dir: str
    snapshot_paths: list[str]
    final_output: str
    total_tokens: int
    max_divergence: float
    branch_id: str | None = None
    parent_run_id: str | None = None
    replay_from: str | None = None
    error: str | None = None

    def mode_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for result in self.results.values():
            counts[result.mode] = counts.get(result.mode, 0) + 1
        return counts

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "task": self.task,
            "run_dir": self.run_dir,
            "audit_path": self.audit_path,
            "snapshot_paths": list(self.snapshot_paths),
            "total_tokens": self.total_tokens,
            "max_divergence": self.max_divergence,
            "mode_counts": self.mode_counts(),
            "branch_id": self.branch_id,
            "parent_run_id": self.parent_run_id,
            "replay_from": self.replay_from,
            "error": self.error,
            "final_output": self.final_output,
            "results": {
                node_id: result.to_dict()
                for node_id, result in self.results.items()
            },
        }
