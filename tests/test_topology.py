from __future__ import annotations

import unittest
from pathlib import Path

import _bootstrap  # noqa: F401

from narthex.errors import TopologyError
from narthex.models import EdgeType, Mode
from narthex.topology.dsl import TopologyBuilder, governor
from narthex.topology.loader import load_topology, loads_topology

ROOT = Path(__file__).resolve().parents[1]


class TopologyTests(unittest.TestCase):
    def test_example_loads_with_stable_order(self) -> None:
        topology = load_topology(ROOT / "examples" / "mixed_delivery.yaml")
        self.assertEqual(topology.name, "mixed-delivery")
        self.assertEqual(
            topology.order(),
            (
                "brief",
                "research",
                "critique",
                "design",
                "production_change",
                "verify",
            ),
        )
        self.assertEqual(topology.default_mode, Mode.BAZAAR)
        self.assertEqual(
            {edge.type for edge in topology.edges},
            {EdgeType.CONSULT, EdgeType.AUTHORIZE},
        )

    def test_cycle_is_rejected(self) -> None:
        data = {
            "name": "cycle",
            "agents": {"a": {"role": "a", "capabilities": ["propose"]}},
            "nodes": [
                {"id": "one", "agent": "a"},
                {"id": "two", "agent": "a"},
            ],
            "edges": [
                {"from": "one", "to": "two"},
                {"from": "two", "to": "one"},
            ],
        }
        with self.assertRaises(TopologyError):
            loads_topology(__import__("json").dumps(data))

    def test_node_cannot_expand_agent_capabilities(self) -> None:
        data = {
            "name": "cap",
            "agents": {"a": {"role": "a", "capabilities": ["propose"]}},
            "nodes": [
                {
                    "id": "one",
                    "agent": "a",
                    "capabilities": ["execute"],
                }
            ],
        }
        with self.assertRaises(TopologyError):
            loads_topology(__import__("json").dumps(data))

    def test_wildcard_agent_capability_covers_exact_node_capability(self) -> None:
        data = {
            "name": "wildcard-cap",
            "agents": {
                "a": {"role": "a", "capabilities": ["propose", "execute.*"]}
            },
            "nodes": [
                {
                    "id": "one",
                    "agent": "a",
                    "capabilities": ["execute.write"],
                }
            ],
        }
        topology = loads_topology(__import__("json").dumps(data))
        self.assertEqual(topology.agents["a"].capabilities, frozenset({"propose", "execute.*"}))

    def test_exact_agent_capability_cannot_cover_wildcard_node_request(self) -> None:
        data = {
            "name": "wildcard-node",
            "agents": {
                "a": {"role": "a", "capabilities": ["propose", "execute.write"]}
            },
            "nodes": [
                {
                    "id": "one",
                    "agent": "a",
                    "capabilities": ["execute.*"],
                }
            ],
        }
        with self.assertRaises(TopologyError):
            loads_topology(__import__("json").dumps(data))

    def test_python_dsl_round_trip(self) -> None:
        topology = (
            TopologyBuilder(
                "dsl",
                default_mode="bazaar",
                governor=governor(divergence_threshold=0.5, max_rounds=2),
            )
            .agent("planner", role="planner", capabilities=["propose", "consult"])
            .agent("writer", role="engineer", capabilities=["propose", "execute"])
            .node("brief", agent="planner", phase="explore", risk=["risk:exploratory"])
            .node(
                "commit",
                agent="writer",
                phase="commit",
                risk=["risk:high"],
                approvers=["planner"],
                approval_scope=["propose"],
            )
            .consult("brief", "commit")
            .build()
        )
        self.assertEqual(topology.order(), ("brief", "commit"))
        self.assertEqual(topology.governor.max_rounds, 2)
        rebuilt = loads_topology(__import__("json").dumps(topology.to_dict()))
        self.assertEqual(rebuilt.to_dict(), topology.to_dict())


if __name__ == "__main__":
    unittest.main()
