from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import _bootstrap  # noqa: F401

from narthex.audit import read_events, verify_audit
from narthex.models import Topology
from narthex.runtime.agents import AgentOutput, AgentRegistry, FunctionAgent, build_demo_registry
from narthex.runtime.approval import RecordingApprovalProvider
from narthex.runtime.engine import NarthexRuntime, RuntimeOptions
from narthex.runtime.tools import ToolAction, build_demo_tools
from narthex.topology.loader import load_topology

ROOT = Path(__file__).resolve().parents[1]
TASK = "为开原基础软件设计一个可回放的变更流程"


def build_mixed_runtime(tmp: str, **kwargs) -> NarthexRuntime:
    topology = load_topology(ROOT / "examples" / "mixed_delivery.yaml")
    return NarthexRuntime(
        topology,
        build_demo_registry(topology),
        tools=build_demo_tools(),
        options=RuntimeOptions(run_root=tmp, run_id=kwargs.pop("run_id", "mixed")),
        **kwargs,
    )


class RuntimeTests(unittest.TestCase):
    def test_runtime_options_merge_preserves_explicit_values(self) -> None:
        base = RuntimeOptions(auto_approve=False, max_rounds=2)
        merged = base.merged(RuntimeOptions(run_id="branch"))
        self.assertFalse(merged.auto_approve)
        self.assertEqual(merged.max_rounds, 2)
        self.assertEqual(merged.run_id, "branch")
        overridden = base.merged(RuntimeOptions(auto_approve=True, max_rounds=5))
        self.assertTrue(overridden.auto_approve)
        self.assertEqual(overridden.max_rounds, 5)

    def test_invalid_agent_output_fails_node_cleanly(self) -> None:
        topology = Topology.from_dict(
            {
                "name": "bad-output",
                "default_mode": "bazaar",
                "agents": {
                    "bad": {"role": "bad", "capabilities": ["propose"]}
                },
                "nodes": [
                    {
                        "id": "explore",
                        "agent": "bad",
                        "phase": "explore",
                        "risk": ["risk:exploratory"],
                    }
                ],
            }
        )

        class BadAgent:
            def __init__(self, spec):
                self.spec = spec

            def propose(self, context):
                return "not an AgentOutput"

            def review(self, context):
                raise AssertionError("not used")

            def revise(self, context, output, reviews):
                return output

        registry = AgentRegistry()
        registry.register(BadAgent(topology.agents["bad"]))
        with tempfile.TemporaryDirectory() as tmp:
            runtime = NarthexRuntime(
                topology,
                registry,
                tools=build_demo_tools(),
                options=RuntimeOptions(run_root=tmp, run_id="bad-output"),
            )
            result = runtime.run("bad output")
            self.assertEqual(result.status, "failed")
            self.assertIn("expected AgentOutput", result.error or "")
            self.assertIn("explore", result.results)
            self.assertEqual(result.results["explore"].status, "failed")

    def test_mixed_topology_switches_modes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = build_mixed_runtime(tmp)
            result = runtime.run(TASK)
            self.assertEqual(result.status, "completed", result.error)
            self.assertEqual(result.mode_counts(), {"bazaar": 4, "cathedral": 2})
            self.assertTrue(Path(result.audit_path).exists())
            self.assertTrue(verify_audit(result.audit_path)[0])
            artifact = Path(result.run_dir) / "artifacts" / "approved-change.md"
            self.assertTrue(artifact.exists())
            verify_tools = result.results["verify"].tools
            self.assertIn("变更步骤", verify_tools[0]["result"]["content"])

            events = read_events(result.audit_path)
            kinds = [event.kind for event in events]
            self.assertIn("mode_switch", kinds)
            self.assertIn("approval_requested", kinds)
            self.assertIn("approve", kinds)
            self.assertIn("endorse", kinds)
            self.assertIn("tool_call", kinds)

            reasons = [
                json.loads(Path(path).read_text(encoding="utf-8"))["reason"]
                for path in result.snapshot_paths
            ]
            self.assertTrue(any("mode_switch" in reason for reason in reasons))
            self.assertTrue(any("approval_gate" in reason for reason in reasons))

    def test_approver_denial_short_circuits_policy_provider(self) -> None:
        topology = Topology.from_dict(
            {
                "name": "approver-deny",
                "agents": {
                    "worker": {
                        "role": "engineer",
                        "capabilities": ["propose", "execute"],
                    },
                    "reviewer": {
                        "role": "reviewer",
                        "capabilities": ["propose", "approve"],
                    },
                },
                "nodes": [
                    {
                        "id": "commit",
                        "agent": "worker",
                        "phase": "commit",
                        "risk": ["risk:high"],
                        "metadata": {"demo_deny": True},
                        "approval": {
                            "approvers": ["reviewer"],
                            "scope": ["propose", "execute"],
                        },
                    }
                ],
            }
        )
        provider = RecordingApprovalProvider({"commit": "approve"})
        with tempfile.TemporaryDirectory() as tmp:
            runtime = NarthexRuntime(
                topology,
                build_demo_registry(topology),
                tools=build_demo_tools(),
                options=RuntimeOptions(run_root=tmp, run_id="deny"),
                approval_provider=provider,
            )
            result = runtime.run("deny demo")
            self.assertEqual(result.status, "blocked")
            self.assertEqual(provider.requests, [])
            self.assertIn("approval deny", result.error or "")

    def test_denied_gate_blocks_and_creates_replay_point(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            provider = RecordingApprovalProvider({"production_change": "deny"})
            runtime = build_mixed_runtime(tmp, approval_provider=provider)
            result = runtime.run(TASK)
            self.assertEqual(result.status, "blocked")
            self.assertIn("production_change", result.error or "")
            self.assertFalse((Path(result.run_dir) / "artifacts" / "approved-change.md").exists())
            self.assertTrue(verify_audit(result.audit_path)[0])
            reasons = [
                json.loads(Path(path).read_text(encoding="utf-8"))["reason"]
                for path in result.snapshot_paths
            ]
            self.assertIn("approval_blocked", reasons)

    def test_bazaar_privileged_action_freezes_into_cathedral(self) -> None:
        topology = Topology.from_dict(
            {
                "name": "freeze",
                "default_mode": "bazaar",
                "agents": {
                    "worker": {
                        "role": "engineer",
                        "capabilities": ["propose", "execute"],
                    }
                },
                "nodes": [
                    {
                        "id": "explore",
                        "agent": "worker",
                        "phase": "explore",
                        "risk": ["risk:exploratory"],
                        "approval": {"scope": ["propose", "execute"]},
                    }
                ],
            }
        )
        spec = topology.agents["worker"]

        def propose(context):
            return AgentOutput(
                content="proposal",
                actions=[ToolAction("work.write", {"path": "x.md", "content": "x"})],
            )

        registry = AgentRegistry()
        registry.register(FunctionAgent(spec, propose))
        with tempfile.TemporaryDirectory() as tmp:
            runtime = NarthexRuntime(
                topology,
                registry,
                tools=build_demo_tools(),
                options=RuntimeOptions(run_root=tmp, run_id="defer"),
            )
            result = runtime.run("demo")
            self.assertEqual(result.status, "completed", result.error)
            self.assertEqual(result.results["explore"].mode, "cathedral")
            action = result.results["explore"].actions[0]
            self.assertEqual(action["status"], "executed")
            self.assertTrue((Path(result.run_dir) / "artifacts" / "x.md").exists())
            audit_text = Path(result.audit_path).read_text(encoding="utf-8")
            self.assertIn("bazaar_privileged_action", audit_text)
            self.assertIn("approval_requested", audit_text)

    def test_approved_scope_missing_execute_fails_node(self) -> None:
        topology = Topology.from_dict(
            {
                "name": "scope",
                "agents": {
                    "worker": {
                        "role": "engineer",
                        "capabilities": ["propose", "execute"],
                    }
                },
                "nodes": [
                    {
                        "id": "commit",
                        "agent": "worker",
                        "phase": "commit",
                        "risk": ["risk:high"],
                        "approval": {"scope": ["propose"]},
                    }
                ],
            }
        )
        spec = topology.agents["worker"]

        def propose(context):
            return AgentOutput(
                content="change",
                actions=[ToolAction("work.write", {"path": "x.md", "content": "x"})],
            )

        registry = AgentRegistry()
        registry.register(FunctionAgent(spec, propose))
        with tempfile.TemporaryDirectory() as tmp:
            runtime = NarthexRuntime(
                topology,
                registry,
                tools=build_demo_tools(),
                options=RuntimeOptions(run_root=tmp, run_id="scope", auto_approve=True),
            )
            result = runtime.run("demo")
            self.assertEqual(result.status, "failed")
            self.assertIn("outside approved scope", result.error or "")
            self.assertFalse((Path(result.run_dir) / "artifacts" / "x.md").exists())


if __name__ == "__main__":
    unittest.main()
