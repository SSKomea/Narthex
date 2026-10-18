"""Python DSL example. Run with: python run.py run examples/dsl_topology.py"""

from __future__ import annotations

from narthex.topology.dsl import TopologyBuilder, governor


topology = (
    TopologyBuilder(
        "dsl-pipeline",
        description="用 Python DSL 定义的 Bazaar -> Cathedral 混合拓扑",
        default_mode="bazaar",
        governor=governor(
            token_budget=3000,
            divergence_threshold=0.9,
            max_rounds=3,
            consensus_threshold=0.6,
        ),
    )
    .agent("planner", role="planner", capabilities=["propose", "consult", "read"])
    .agent("engineer", role="engineer", capabilities=["propose", "execute", "read"])
    .agent("verifier", role="verifier", capabilities=["propose", "approve", "read"])
    .node(
        "brief",
        agent="planner",
        phase="explore",
        risk=["risk:exploratory"],
        task="用 Bazaar 模式探索目标与约束",
    )
    .node(
        "release",
        agent="engineer",
        phase="commit",
        risk=["risk:high"],
        task="把探索结果冻结为可审批变更",
        capabilities=["propose", "execute"],
        approvers=["verifier"],
        approval_scope=["propose", "execute"],
    )
    .node(
        "verify",
        agent="verifier",
        phase="verify",
        risk=["risk:high"],
        task="验证审批边界与写入结果",
        capabilities=["propose", "read"],
        approvers=["verifier"],
        approval_scope=["propose", "read"],
    )
    .consult("brief", "release")
    .authorize("release", "verify")
    .build()
)
