from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import _bootstrap  # noqa: F401

from narthex.topology import yaml_lite


class YamlLiteTests(unittest.TestCase):
    def test_nested_document(self) -> None:
        document = """
# comment
name: demo
enabled: true
count: 3
ratio: 0.25
tags: [risk:high, "risk: exploratory", 7]
agents:
  planner:
    role: planner
    capabilities:
      - propose
      - consult
nodes:
  - id: brief
    agent: planner
    risk: [risk:exploratory]
    metadata:
      note: "a: b # not a comment"
  - id: commit
    agent: planner
edges:
  - from: brief
    to: commit
    type: authorize
"""
        data = yaml_lite.loads(document)
        self.assertEqual(data["name"], "demo")
        self.assertIs(data["enabled"], True)
        self.assertEqual(data["count"], 3)
        self.assertAlmostEqual(data["ratio"], 0.25)
        self.assertEqual(data["tags"], ["risk:high", "risk: exploratory", 7])
        self.assertEqual(data["agents"]["planner"]["capabilities"], ["propose", "consult"])
        self.assertEqual(data["nodes"][0]["metadata"]["note"], "a: b # not a comment")
        self.assertEqual(data["edges"][0]["type"], "authorize")

    def test_block_scalar(self) -> None:
        data = yaml_lite.loads("text: |\n  first line\n  second line\n")
        self.assertEqual(data["text"], "first line\nsecond line\n")

    def test_block_scalar_preserves_hashes_and_blank_lines(self) -> None:
        data = yaml_lite.loads(
            "text: |\n"
            "  first # not a comment\n"
            "  second\n"
            "\n"
            "  fourth\n"
        )
        self.assertEqual(data["text"], "first # not a comment\nsecond\n\nfourth\n")

    def test_json_file_is_supported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "topology.json"
            path.write_text(json.dumps({"name": "json"}), encoding="utf-8")
            self.assertEqual(yaml_lite.load(path), {"name": "json"})

    def test_bad_indentation_has_line_number(self) -> None:
        with self.assertRaisesRegex(yaml_lite.YamlLiteError, "line 3"):
            yaml_lite.loads("a:\n  b: 1\n c: 2\n")


if __name__ == "__main__":
    unittest.main()
