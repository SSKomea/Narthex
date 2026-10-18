"""The Topology Governor.

The Governor turns task signals into a per-node governance decision.  It is
the only place that decides whether a node runs in Cathedral or Bazaar mode.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..models import Mode, NodeSpec, Topology


@dataclass(frozen=True)
class ExecutionSignals:
    """Runtime signals available before a node is executed."""

    incoming_consult: tuple[str, ...] = ()
    incoming_authorize: tuple[str, ...] = ()
    cumulative_tokens: int = 0
    divergence: float = 0.0
    round_index: int = 0
    challenge_ratio: float = 0.0
    no_consensus: bool = False


@dataclass(frozen=True)
class GovernanceDecision:
    mode: Mode
    reasons: tuple[str, ...]
    forced: bool = False
    snapshot_required: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode.value,
            "reasons": list(self.reasons),
            "forced": self.forced,
            "snapshot_required": self.snapshot_required,
        }


class TopologyGovernor:
    """Applies risk, phase, cost, and divergence rules in a fixed order."""

    def __init__(self, topology: Topology) -> None:
        self.topology = topology
        self.config = topology.governor

    def decide(
        self,
        node: NodeSpec,
        signals: ExecutionSignals | None = None,
    ) -> GovernanceDecision:
        signals = signals or ExecutionSignals()
        high_risk = sorted(set(node.risk) & set(self.config.high_risk_tags))
        exploratory = sorted(set(node.risk) & set(self.config.exploratory_tags))

        # Safety-critical paths cannot be downgraded to Bazaar by a node hint.
        if node.phase in self.config.commit_phases:
            return GovernanceDecision(
                mode=Mode.CATHEDRAL,
                reasons=(f"commit_phase:{node.phase}",),
                forced=True,
                snapshot_required=True,
            )
        if signals.incoming_authorize:
            return GovernanceDecision(
                mode=Mode.CATHEDRAL,
                reasons=(
                    "authorize_edge:" + ",".join(signals.incoming_authorize),
                ),
                forced=True,
                snapshot_required=True,
            )
        if high_risk:
            return GovernanceDecision(
                mode=Mode.CATHEDRAL,
                reasons=tuple(f"high_risk:{tag}" for tag in high_risk),
                forced=True,
                snapshot_required=True,
            )
        if node.mode is Mode.CATHEDRAL:
            return GovernanceDecision(
                mode=Mode.CATHEDRAL,
                reasons=("explicit_node_mode:cathedral",),
                forced=False,
                snapshot_required=False,
            )
        if signals.cumulative_tokens > self.config.token_budget:
            return GovernanceDecision(
                mode=Mode.CATHEDRAL,
                reasons=(
                    f"token_budget_exceeded:{signals.cumulative_tokens}>"
                    f"{self.config.token_budget}",
                ),
                forced=True,
                snapshot_required=True,
            )
        if signals.divergence > self.config.divergence_threshold:
            return GovernanceDecision(
                mode=Mode.CATHEDRAL,
                reasons=(
                    f"divergence_exceeded:{signals.divergence:.3f}>"
                    f"{self.config.divergence_threshold:.3f}",
                ),
                forced=True,
                snapshot_required=True,
            )
        if signals.no_consensus:
            return GovernanceDecision(
                mode=Mode.CATHEDRAL,
                reasons=("bazaar_no_consensus",),
                forced=True,
                snapshot_required=True,
            )
        if exploratory or node.mode is Mode.BAZAAR:
            reasons = (
                ("explicit_node_mode:bazaar",)
                if node.mode is Mode.BAZAAR
                else tuple(f"exploratory:{tag}" for tag in exploratory)
            )
            return GovernanceDecision(
                mode=Mode.BAZAAR,
                reasons=reasons,
                forced=False,
                snapshot_required=False,
            )
        return GovernanceDecision(
            mode=self.topology.default_mode,
            reasons=(f"topology_default:{self.topology.default_mode.value}",),
            forced=False,
            snapshot_required=False,
        )
