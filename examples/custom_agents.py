"""Programmatic example with custom function agents and an explicit gate.

Run with: python examples/custom_agents.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from narthex import (  # noqa: E402
    AgentRegistry,
    AgentSpec,
    FunctionAgent,
    NarthexRuntime,
    RecordingApprovalProvider,
    RuntimeOptions,
    ToolAction,
    Topology,
    build_demo_tools,
)
from narthex.runtime.agents import AgentOutput  # noqa: E402


def planner_propose(context):
    note = f"探索任务：{context.instruction}"
    # Direct proposal-time tool use is audited and never requires execute.
    context.tools.invoke("work.note", text=note)
    return AgentOutput(content=note, confidence=0.9)


def engineer_propose(context):
    return AgentOutput(
        content=f"已收敛方案：{context.instruction}",
        confidence=0.9,
        actions=[
            ToolAction(
                "work.write",
                {"path": "custom-change.md", "content": context.instruction},
                "写入已审批变更",
            )
        ],
    )


def reviewer_review(context):
    from narthex.runtime.agents import Review, ReviewDecision

    return Review(
        ReviewDecision.APPROVE,
        "确认变更范围、回滚路径和写入目标均符合要求",
        confidence=0.95,
    )


def main() -> None:
    topology = Topology.from_dict(
        {
            "name": "custom-agents",
            "default_mode": "bazaar",
            "agents": {
                "planner": {"role": "planner", "capabilities": ["propose", "consult", "read"]},
                "engineer": {
                    "role": "engineer",
                    "capabilities": ["propose", "execute", "read"],
                },
                "reviewer": {
                    "role": "reviewer",
                    "capabilities": ["propose", "approve", "read"],
                },
            },
            "nodes": [
                {
                    "id": "explore",
                    "agent": "planner",
                    "phase": "explore",
                    "risk": ["risk:exploratory"],
                    "task": "探索变更边界",
                },
                {
                    "id": "release",
                    "agent": "engineer",
                    "phase": "commit",
                    "risk": ["risk:high"],
                    "task": "提交生产变更",
                    "capabilities": ["propose", "execute"],
                    "approval": {
                        "approvers": ["reviewer"],
                        "scope": ["propose", "execute"],
                    },
                },
            ],
            "edges": [{"from": "explore", "to": "release", "type": "authorize"}],
        }
    )
    registry = AgentRegistry()
    registry.register(
        FunctionAgent(AgentSpec("planner", "planner", frozenset({"propose", "consult", "read"})), planner_propose)
    )
    registry.register(
        FunctionAgent(AgentSpec("engineer", "engineer", frozenset({"propose", "execute", "read"})), engineer_propose)
    )
    registry.register(
        FunctionAgent(
            AgentSpec("reviewer", "reviewer", frozenset({"propose", "approve", "read"})),
            lambda context: AgentOutput("审批材料已准备", 0.9),
            reviewer_review,
        )
    )

    with tempfile.TemporaryDirectory() as tmp:
        runtime = NarthexRuntime(
            topology,
            registry,
            tools=build_demo_tools(),
            options=RuntimeOptions(run_root=tmp, run_id="custom"),
            approval_provider=RecordingApprovalProvider({"release": "approve"}),
        )
        result = runtime.run("实现一个可审计的发布变更")
        print(result.status)
        print(result.mode_counts())
        print(result.final_output)
        print(result.run_dir)


if __name__ == "__main__":
    main()
