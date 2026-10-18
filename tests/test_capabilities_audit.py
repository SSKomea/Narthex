from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import _bootstrap  # noqa: F401

from narthex.audit import AuditLog, verify_audit
from narthex.capabilities import CapabilityPolicy, capability_grants, intersect_capabilities
from narthex.errors import ToolDenied
from narthex.events import EventKind
from narthex.models import AgentSpec, Mode, NodeSpec
from narthex.runtime.tools import ToolInvoker, build_demo_tools


class CapabilityTests(unittest.TestCase):
    def test_bazaar_removes_execution_capability(self) -> None:
        agent = AgentSpec("engineer", "engineer", frozenset({"propose", "execute", "read"}))
        node = NodeSpec.from_dict(
            {
                "id": "commit",
                "agent": "engineer",
                "phase": "commit",
                "approval": {"scope": ["propose", "execute"]},
            }
        )
        policy = CapabilityPolicy()
        bazaar = policy.effective_capabilities(agent, node, Mode.BAZAAR)
        self.assertIn("propose", bazaar)
        self.assertIn("read", bazaar)
        self.assertNotIn("execute", bazaar)
        cathedral = policy.effective_capabilities(
            agent, node, Mode.CATHEDRAL, approved=True
        )
        self.assertIn("execute", cathedral)

    def test_bazaar_whitelist_cannot_restore_execute(self) -> None:
        agent = AgentSpec(
            "engineer",
            "engineer",
            frozenset({"propose", "propose.journal", "execute", "read"}),
        )
        node = NodeSpec.from_dict(
            {
                "id": "explore",
                "agent": "engineer",
                "phase": "explore",
                "metadata": {
                    "bazaar_capabilities": ["execute", "propose.journal", "read"]
                },
            }
        )
        caps = CapabilityPolicy().effective_capabilities(agent, node, Mode.BAZAAR)
        self.assertIn("propose.journal", caps)
        self.assertIn("read", caps)
        self.assertNotIn("execute", caps)

    def test_bazaar_prefix_matching_is_segment_aware(self) -> None:
        agent = AgentSpec(
            "worker", "worker", frozenset({"propose", "readwrite", "read.audit"})
        )
        node = NodeSpec("explore", "worker", phase="explore")
        caps = CapabilityPolicy().effective_capabilities(agent, node, Mode.BAZAAR)
        self.assertIn("propose", caps)
        self.assertIn("read.audit", caps)
        self.assertNotIn("readwrite", caps)

    def test_approval_scope_narrows_cathedral_capabilities(self) -> None:
        agent = AgentSpec(
            "engineer", "engineer", frozenset({"propose", "execute", "read"})
        )
        node = NodeSpec.from_dict(
            {
                "id": "commit",
                "agent": "engineer",
                "phase": "commit",
                "approval": {"scope": ["propose", "read"]},
            }
        )
        policy = CapabilityPolicy()
        caps = policy.effective_capabilities(agent, node, Mode.CATHEDRAL, approved=True)
        self.assertEqual(caps, frozenset({"propose", "read"}))

    def test_omitted_scope_is_least_privilege(self) -> None:
        agent = AgentSpec(
            "engineer", "engineer", frozenset({"propose", "execute", "read"})
        )
        node = NodeSpec.from_dict(
            {"id": "commit", "agent": "engineer", "phase": "commit"}
        )
        caps = CapabilityPolicy().effective_capabilities(
            agent, node, Mode.CATHEDRAL, approved=True
        )
        self.assertIn("propose", caps)
        self.assertNotIn("execute", caps)

    def test_wildcard_capability(self) -> None:
        self.assertTrue(capability_grants({"execute.*"}, "execute.write"))
        self.assertFalse(capability_grants({"execute.*"}, "propose.write"))
        self.assertTrue(capability_grants({"*"}, "anything"))

    def test_wildcard_narrowing_keeps_exact_capability(self) -> None:
        narrowed = intersect_capabilities({"execute.*"}, {"execute.write"})
        self.assertEqual(narrowed, frozenset({"execute.write"}))
        widened = intersect_capabilities({"execute.write"}, {"execute.*"})
        self.assertEqual(widened, frozenset({"execute.write"}))

    def test_tool_invoker_blocks_unapproved_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audit_path = Path(tmp) / "audit.jsonl"
            audit = AuditLog(audit_path, "run")
            invoker = ToolInvoker(
                build_demo_tools(),
                run_id="run",
                node_id="node",
                agent_id="agent",
                mode=Mode.BAZAAR,
                capabilities=frozenset({"propose", "read"}),
                artifacts_dir=Path(tmp),
                audit=audit,
            )
            with self.assertRaises(ToolDenied):
                invoker.invoke("work.write", path="x.md", content="nope")
            audit.close()
            text = audit_path.read_text(encoding="utf-8")
            self.assertIn(EventKind.TOOL_DENIED, text)
            self.assertFalse((Path(tmp) / "x.md").exists())
            self.assertTrue(verify_audit(audit_path)[0])


class AuditTests(unittest.TestCase):
    def test_hash_chain_and_tamper_detection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "audit.jsonl"
            with AuditLog(path, "run") as audit:
                audit.emit(EventKind.RUN_STARTED, task="demo")
                audit.emit(EventKind.PROPOSE, node_id="n1", agent_id="a1", content="hello")
            ok, message = verify_audit(path)
            self.assertTrue(ok, message)
            lines = path.read_text(encoding="utf-8").splitlines()
            lines[1] = lines[1].replace('"hello"', '"tampered"')
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            ok, _ = verify_audit(path)
            self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
