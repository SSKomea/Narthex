from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import _bootstrap  # noqa: F401

from narthex.audit import read_events, verify_audit
from narthex.runtime.agents import build_demo_registry
from narthex.runtime.engine import NarthexRuntime, RuntimeOptions
from narthex.runtime.tools import build_demo_tools
from narthex.topology.loader import load_topology

ROOT = Path(__file__).resolve().parents[1]


class ReplayTests(unittest.TestCase):
    def test_replay_from_approval_gate_creates_branch(self) -> None:
        topology = load_topology(ROOT / "examples" / "mixed_delivery.yaml")
        with tempfile.TemporaryDirectory() as tmp:
            runtime = NarthexRuntime(
                topology,
                build_demo_registry(topology),
                tools=build_demo_tools(),
                options=RuntimeOptions(run_root=tmp, run_id="original"),
            )
            first = runtime.run("replay branch")
            self.assertEqual(first.status, "completed")
            gate = next(
                path
                for path in first.snapshot_paths
                if "production_change-approval_gate" in path
            )
            snapshot = json.loads(Path(gate).read_text(encoding="utf-8"))
            self.assertEqual(snapshot["node_id"], "production_change")

            second = runtime.replay(gate, task="replay with revised context")
            self.assertEqual(second.status, "completed", second.error)
            self.assertEqual(second.parent_run_id, first.run_id)
            self.assertEqual(second.replay_from, "production_change")
            self.assertIn("replay-", second.branch_id or "")
            self.assertTrue(verify_audit(second.audit_path)[0])
            kinds = [event.kind for event in read_events(second.audit_path)]
            self.assertIn("replay_started", kinds)
            self.assertIn("replay_completed", kinds)
            # Results before the branch point are preserved.
            self.assertIn("brief", second.results)
            self.assertIn("design", second.results)


if __name__ == "__main__":
    unittest.main()
