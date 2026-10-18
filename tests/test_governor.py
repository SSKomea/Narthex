from __future__ import annotations

import unittest

import _bootstrap  # noqa: F401

from narthex.governance import ExecutionSignals, TopologyGovernor
from narthex.models import Mode, Topology


def make_topology() -> Topology:
    return Topology.from_dict(
        {
            "name": "governor",
            "default_mode": "bazaar",
            "governor": {
                "token_budget": 100,
                "divergence_threshold": 0.5,
                "commit_phases": ["commit", "production"],
            },
            "agents": {
                "a": {"role": "worker", "capabilities": ["propose"]},
                "b": {"role": "reviewer", "capabilities": ["propose", "approve"]},
            },
            "nodes": [
                {"id": "explore", "agent": "a", "phase": "explore", "risk": ["risk:exploratory"]},
                {"id": "unsafe", "agent": "a", "phase": "explore", "risk": ["risk:high"]},
                {"id": "commit", "agent": "a", "phase": "commit", "mode": "bazaar"},
                {"id": "authorized", "agent": "a", "phase": "work"},
                {"id": "review", "agent": "b", "phase": "work"},
            ],
            "edges": [
                {"from": "explore", "to": "authorized", "type": "authorize"},
                {"from": "authorized", "to": "review", "type": "consult"},
            ],
        }
    )


class GovernorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.topology = make_topology()
        self.governor = TopologyGovernor(self.topology)

    def test_exploratory_uses_bazaar(self) -> None:
        decision = self.governor.decide(self.topology.nodes["explore"])
        self.assertEqual(decision.mode, Mode.BAZAAR)
        self.assertIn("exploratory:risk:exploratory", decision.reasons)

    def test_high_risk_forces_cathedral(self) -> None:
        decision = self.governor.decide(self.topology.nodes["unsafe"])
        self.assertEqual(decision.mode, Mode.CATHEDRAL)
        self.assertTrue(decision.forced)
        self.assertIn("high_risk:risk:high", decision.reasons)

    def test_commit_phase_overrides_explicit_bazaar(self) -> None:
        decision = self.governor.decide(self.topology.nodes["commit"])
        self.assertEqual(decision.mode, Mode.CATHEDRAL)
        self.assertIn("commit_phase:commit", decision.reasons)
        self.assertTrue(decision.snapshot_required)

    def test_authorize_edge_forces_cathedral(self) -> None:
        decision = self.governor.decide(
            self.topology.nodes["authorized"],
            ExecutionSignals(incoming_authorize=("explore",)),
        )
        self.assertEqual(decision.mode, Mode.CATHEDRAL)
        self.assertIn("authorize_edge:explore", decision.reasons)

    def test_cost_and_divergence_degrade_bazaar(self) -> None:
        token_decision = self.governor.decide(
            self.topology.nodes["explore"],
            ExecutionSignals(cumulative_tokens=101),
        )
        self.assertEqual(token_decision.mode, Mode.CATHEDRAL)
        self.assertIn("token_budget_exceeded", token_decision.reasons[0])

        divergence_decision = self.governor.decide(
            self.topology.nodes["explore"],
            ExecutionSignals(divergence=0.8),
        )
        self.assertEqual(divergence_decision.mode, Mode.CATHEDRAL)
        self.assertIn("divergence_exceeded", divergence_decision.reasons[0])

    def test_no_consensus_escalates(self) -> None:
        decision = self.governor.decide(
            self.topology.nodes["explore"],
            ExecutionSignals(no_consensus=True),
        )
        self.assertEqual(decision.mode, Mode.CATHEDRAL)
        self.assertIn("bazaar_no_consensus", decision.reasons)


if __name__ == "__main__":
    unittest.main()
