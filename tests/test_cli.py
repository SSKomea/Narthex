from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import _bootstrap  # noqa: F401

from narthex.audit import verify_audit
from narthex.cli import main

ROOT = Path(__file__).resolve().parents[1]


class CliTests(unittest.TestCase):
    def test_validate(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(["validate", str(ROOT / "examples" / "mixed_delivery.yaml")])
        self.assertEqual(code, 0)
        self.assertIn("mixed-delivery", output.getvalue())

    def test_demo_run_and_verify(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main(
                    [
                        "demo",
                        "--run-root",
                        tmp,
                        "--run-id",
                        "cli",
                        "--json",
                    ]
                )
            self.assertEqual(code, 0)
            data = json.loads(output.getvalue())
            self.assertEqual(data["status"], "completed")
            self.assertTrue(verify_audit(data["audit_path"])[0])

            inspect_output = io.StringIO()
            with contextlib.redirect_stdout(inspect_output):
                code = main(["inspect", data["run_dir"]])
            self.assertEqual(code, 0)
            inspected = json.loads(inspect_output.getvalue())
            self.assertEqual(inspected["summary"]["run_id"], "cli")

            verify_output = io.StringIO()
            with contextlib.redirect_stdout(verify_output):
                code = main(["verify", data["audit_path"]])
            self.assertEqual(code, 0)
            self.assertIn("OK", verify_output.getvalue())

            gate = next(
                path
                for path in data["snapshot_paths"]
                if "production_change-approval_gate" in path
            )
            blocked_output = io.StringIO()
            with contextlib.redirect_stdout(blocked_output):
                code = main(
                    [
                        "replay",
                        gate,
                        "--run-root",
                        tmp,
                        "--run-id",
                        "replay-blocked",
                        "--no-auto-approve",
                        "--json",
                    ]
                )
            self.assertEqual(code, 2)
            blocked = json.loads(blocked_output.getvalue())
            self.assertEqual(blocked["status"], "blocked")

            approved_output = io.StringIO()
            with contextlib.redirect_stdout(approved_output):
                code = main(
                    [
                        "replay",
                        gate,
                        "--run-root",
                        tmp,
                        "--run-id",
                        "replay-approved",
                        "--no-auto-approve",
                        "--approve-node",
                        "production_change",
                        "--approve-node",
                        "verify",
                        "--json",
                    ]
                )
            self.assertEqual(code, 0)
            approved = json.loads(approved_output.getvalue())
            self.assertEqual(approved["status"], "completed")


if __name__ == "__main__":
    unittest.main()
