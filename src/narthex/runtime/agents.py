"""Agent protocol, registry, and deterministic demo agents.

Narthex does not require a particular LLM SDK.  An agent is any object that
implements ``propose`` and ``review``.  The demo agents make the project
runnable end-to-end without an API key.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Mapping, Protocol

from ..errors import AgentResolutionError
from ..models import AgentSpec, Mode, NodeSpec, Topology
from .tools import ToolAction, ToolInvoker


def estimate_tokens(text: str) -> int:
    """A stable, model-agnostic token estimate for budget signals."""
    return max(1, (len(text) + 3) // 4)


class ReviewDecision(str, Enum):
    ENDORSE = "endorse"
    CHALLENGE = "challenge"
    APPROVE = "approve"
    DENY = "deny"
    ESCALATE = "escalate"

    @classmethod
    def parse(cls, value: "ReviewDecision | str") -> "ReviewDecision":
        if isinstance(value, cls):
            return value
        return cls(str(value).strip().lower())


@dataclass
class AgentOutput:
    content: str
    confidence: float = 0.8
    token_cost: int | None = None
    actions: list[ToolAction] = field(default_factory=list)
    artifacts: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.confidence = min(1.0, max(0.0, float(self.confidence)))
        if self.token_cost is None:
            self.token_cost = estimate_tokens(self.content)
        else:
            self.token_cost = max(0, int(self.token_cost))

    def to_dict(self) -> dict[str, Any]:
        return {
            "content": self.content,
            "confidence": self.confidence,
            "token_cost": self.token_cost,
            "actions": [action.to_dict() for action in self.actions],
            "artifacts": dict(self.artifacts),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class Review:
    decision: ReviewDecision
    rationale: str = ""
    confidence: float = 0.8
    token_cost: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.decision, ReviewDecision):
            object.__setattr__(self, "decision", ReviewDecision.parse(self.decision))
        object.__setattr__(
            self, "confidence", min(1.0, max(0.0, float(self.confidence)))
        )
        object.__setattr__(self, "token_cost", max(0, int(self.token_cost)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision.value,
            "rationale": self.rationale,
            "confidence": self.confidence,
            "token_cost": self.token_cost,
        }


@dataclass(frozen=True)
class AgentContext:
    run_id: str
    topology_name: str
    node: NodeSpec
    agent: AgentSpec
    task: str
    mode: Mode
    capabilities: frozenset[str]
    tools: ToolInvoker
    upstream: Mapping[str, dict[str, Any]] = field(default_factory=dict)
    round_index: int = 0

    @property
    def instruction(self) -> str:
        return self.node.task or self.task


@dataclass(frozen=True)
class ReviewContext:
    run_id: str
    topology_name: str
    node: NodeSpec
    agent: AgentSpec
    mode: Mode
    proposal: AgentOutput
    proposal_agent_id: str
    task: str
    upstream: Mapping[str, dict[str, Any]] = field(default_factory=dict)
    round_index: int = 0


class Agent(Protocol):
    spec: AgentSpec

    def propose(self, context: AgentContext) -> AgentOutput: ...

    def review(self, context: ReviewContext) -> Review: ...

    def revise(
        self,
        context: AgentContext,
        output: AgentOutput,
        reviews: list[Review],
    ) -> AgentOutput: ...


class BaseAgent:
    """Convenience base with safe default review/revise behavior."""

    def __init__(self, spec: AgentSpec) -> None:
        self.spec = spec

    def propose(self, context: AgentContext) -> AgentOutput:
        raise NotImplementedError

    def review(self, context: ReviewContext) -> Review:
        decision = (
            ReviewDecision.APPROVE
            if context.mode is Mode.CATHEDRAL
            else ReviewDecision.ENDORSE
        )
        return Review(
            decision=decision,
            rationale="默认审查：未发现阻断性问题",
            token_cost=2,
        )

    def revise(
        self,
        context: AgentContext,
        output: AgentOutput,
        reviews: list[Review],
    ) -> AgentOutput:
        challenges = [
            review.rationale
            for review in reviews
            if review.decision is ReviewDecision.CHALLENGE
        ]
        if not challenges:
            return output
        addition = "\n修订说明：已吸收评审意见 -> " + "；".join(challenges[:3])
        content = output.content + addition
        return AgentOutput(
            content=content,
            confidence=min(1.0, output.confidence + 0.05),
            actions=output.actions,
            artifacts=dict(output.artifacts),
            metadata={**output.metadata, "revised": True},
        )


class FunctionAgent(BaseAgent):
    """Adapter for ordinary Python functions."""

    def __init__(
        self,
        spec: AgentSpec,
        propose_fn: Callable[[AgentContext], AgentOutput | str],
        review_fn: Callable[[ReviewContext], Review | ReviewDecision | str] | None = None,
        revise_fn: Callable[[AgentContext, AgentOutput, list[Review]], AgentOutput] | None = None,
    ) -> None:
        super().__init__(spec)
        self._propose_fn = propose_fn
        self._review_fn = review_fn
        self._revise_fn = revise_fn

    def propose(self, context: AgentContext) -> AgentOutput:
        result = self._propose_fn(context)
        if isinstance(result, AgentOutput):
            return result
        return AgentOutput(content=str(result))

    def review(self, context: ReviewContext) -> Review:
        if self._review_fn is None:
            return super().review(context)
        result = self._review_fn(context)
        if isinstance(result, Review):
            return result
        return Review(decision=ReviewDecision.parse(result))

    def revise(
        self,
        context: AgentContext,
        output: AgentOutput,
        reviews: list[Review],
    ) -> AgentOutput:
        if self._revise_fn is None:
            return super().revise(context, output, reviews)
        return self._revise_fn(context, output, reviews)


class DemoAgent(BaseAgent):
    """Deterministic role-based agent used by the CLI and examples."""

    def propose(self, context: AgentContext) -> AgentOutput:
        role = self.spec.role.lower()
        task = context.instruction
        upstream_lines = []
        for node_id, view in list(context.upstream.items())[-6:]:
            summary = " ".join(str(view.get("output", "")).split())
            if len(summary) > 120:
                summary = summary[:120] + "..."
            upstream_lines.append(f"- {node_id}: {summary}")
        upstream_text = "\n".join(upstream_lines) or "- 无上游产物"
        action: list[ToolAction] = []

        if "plan" in role or "brief" in role:
            content = (
                f"[探索简报]\n任务：{task}\n"
                "目标：明确成功标准、约束与可验证的交付边界。\n"
                f"上游输入：\n{upstream_text}"
            )
            action.append(ToolAction("work.note", {"text": content}, "记录探索简报"))
            confidence = 0.84
        elif "research" in role:
            content = (
                f"[方案调研]\n任务：{task}\n"
                "候选方案 A：最小改动、快速验证。\n"
                "候选方案 B：模块化重构、便于审计与回放。\n"
                "候选方案 C：保守发布、保留人工门禁。\n"
                f"上游输入：\n{upstream_text}"
            )
            confidence = 0.8
        elif "critic" in role or "review" in role and "manager" not in role:
            content = (
                f"[批判审查]\n任务：{task}\n"
                "主要风险：隐含假设、回滚路径、权限边界与可观测性。\n"
                "建议：将执行变更冻结到 Cathedral 审批链，并保留状态快照。\n"
                f"上游输入：\n{upstream_text}"
            )
            confidence = 0.72
        elif "engineer" in role or "developer" in role:
            if context.node.phase in {
                "commit",
                "production",
                "deploy",
                "release",
                "execute",
            }:
                content = (
                    f"[提交方案]\n任务：{task}\n"
                    "变更步骤：1) 冻结 Bazaar 产物；2) 进入 Cathedral 审批；"
                    "3) 最小权限落盘；4) 生成可回放快照。\n"
                    f"上游输入：\n{upstream_text}"
                )
                action.append(
                    ToolAction(
                        "work.write",
                        {"path": "approved-change.md", "content": content},
                        "写入已审批的变更说明",
                    )
                )
                confidence = 0.86
            else:
                content = (
                    f"[设计整合]\n任务：{task}\n"
                    "综合探索结论：采用模块化边界，保留能力白名单、统一审计流与"
                    "审批失败后的分支回放点。\n"
                    f"上游输入：\n{upstream_text}"
                )
                confidence = 0.82
        elif "verif" in role or "qa" in role or "test" in role:
            content = (
                f"[验收结论]\n任务：{task}\n"
                "验收项：审批事件可追溯、写入权限仅在批准后生效、"
                "审计哈希链完整、快照可回放。\n"
                f"上游输入：\n{upstream_text}"
            )
            action.append(
                ToolAction(
                    "work.read",
                    {"path": "approved-change.md", "content": content},
                    "读取已审批变更并记录验收依据",
                )
            )
            confidence = 0.88
        elif "release" in role or "manager" in role:
            content = (
                f"[发布审查]\n任务：{task}\n"
                "检查发布范围、回滚方案与最小权限范围。\n"
                f"上游输入：\n{upstream_text}"
            )
            confidence = 0.85
        else:
            content = (
                f"[{self.spec.role}]\n任务：{task}\n"
                f"当前节点治理模式：{context.mode.value}。\n"
                f"上游输入：\n{upstream_text}"
            )
            confidence = 0.75

        return AgentOutput(
            content=content,
            confidence=confidence,
            actions=action,
            metadata={"role": self.spec.role, "phase": context.node.phase},
        )

    def review(self, context: ReviewContext) -> Review:
        role = self.spec.role.lower()
        if context.mode is Mode.CATHEDRAL:
            if context.node.metadata.get("demo_escalate"):
                return Review(
                    ReviewDecision.ESCALATE,
                    "演示信号要求人工升级处理",
                    token_cost=3,
                )
            if context.node.metadata.get("demo_deny"):
                return Review(
                    ReviewDecision.DENY,
                    "演示信号明确拒绝该变更",
                    token_cost=3,
                )
            return Review(
                ReviewDecision.APPROVE,
                f"{self.spec.role} 审查通过：变更范围与批准能力一致",
                confidence=0.88,
                token_cost=3,
            )
        if (
            context.node.metadata.get("challenge_first_round")
            and context.round_index == 0
            and "critic" in role
        ):
            return Review(
                ReviewDecision.CHALLENGE,
                "首轮要求补充回滚与权限边界证据",
                confidence=0.7,
                token_cost=2,
            )
        if context.proposal.confidence < 0.45:
            return Review(
                ReviewDecision.CHALLENGE,
                "提案置信度过低",
                confidence=0.7,
                token_cost=2,
            )
        return Review(
            ReviewDecision.ENDORSE,
            f"{self.spec.role} 认可该提案的可验证性与当前风险水平",
            confidence=0.82,
            token_cost=2,
        )


class AgentRegistry:
    def __init__(self, agents: Mapping[str, Agent] | None = None) -> None:
        self._agents: dict[str, Agent] = dict(agents or {})

    def register(self, agent: Agent) -> "AgentRegistry":
        agent_id = agent.spec.id
        if agent_id in self._agents:
            raise ValueError(f"duplicate agent registration: {agent_id!r}")
        self._agents[agent_id] = agent
        return self

    def get(self, agent_id: str) -> Agent:
        try:
            return self._agents[agent_id]
        except KeyError as exc:
            raise AgentResolutionError(f"agent not registered: {agent_id!r}") from exc

    def has(self, agent_id: str) -> bool:
        return agent_id in self._agents

    def ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._agents))


def build_demo_registry(topology: Topology) -> AgentRegistry:
    registry = AgentRegistry()
    for spec in topology.agents.values():
        registry.register(DemoAgent(spec))
    return registry
