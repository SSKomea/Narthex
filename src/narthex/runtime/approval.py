"""Approval gate policy for Cathedral nodes."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from ..models import NodeSpec
from .agents import AgentOutput, Review, ReviewDecision


@dataclass(frozen=True)
class ApprovalContext:
    run_id: str
    topology_name: str
    node: NodeSpec
    proposal: AgentOutput
    proposal_agent_id: str
    task: str
    approver_reviews: tuple[Review, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


class ApprovalProvider(Protocol):
    def decide(self, context: ApprovalContext) -> Review: ...


class AutoApprovalProvider:
    """Policy gate used by the demo.

    ``auto_approve=True`` approves every node.  When disabled, only nodes listed
    in ``approved_nodes`` pass and all others escalate for a human decision.
    """

    def __init__(
        self,
        auto_approve: bool = True,
        approved_nodes: frozenset[str] | set[str] | tuple[str, ...] = (),
        denied_nodes: frozenset[str] | set[str] | tuple[str, ...] = (),
    ) -> None:
        self.auto_approve = auto_approve
        self.approved_nodes = frozenset(approved_nodes)
        self.denied_nodes = frozenset(denied_nodes)

    def decide(self, context: ApprovalContext) -> Review:
        if context.node.id in self.denied_nodes:
            return Review(
                ReviewDecision.DENY,
                "节点被显式列入拒绝列表",
                token_cost=1,
            )
        if self.auto_approve or context.node.id in self.approved_nodes:
            return Review(
                ReviewDecision.APPROVE,
                "策略审批通过" if self.auto_approve else "节点已由调用者预先批准",
                token_cost=1,
            )
        return Review(
            ReviewDecision.ESCALATE,
            "未提供人工审批决定，升级处理",
            token_cost=1,
        )


class RecordingApprovalProvider:
    """Simple provider that queues decisions for interactive or test use."""

    def __init__(self, decisions: dict[str, ReviewDecision | str] | None = None) -> None:
        self.decisions = {
            node_id: ReviewDecision.parse(decision)
            for node_id, decision in (decisions or {}).items()
        }
        self.requests: list[ApprovalContext] = []

    def decide(self, context: ApprovalContext) -> Review:
        self.requests.append(context)
        decision = self.decisions.get(context.node.id, ReviewDecision.ESCALATE)
        return Review(decision, f"录制审批决定: {decision.value}", token_cost=1)
