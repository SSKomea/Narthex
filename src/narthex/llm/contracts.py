"""Canonical prompt contracts and response parsers.

Every model receives the same rubric, the same output schema and the same
language rule, so behavioral differences between backends collapse into
"how well the model follows the contract" instead of changing the runtime's
governance semantics.
"""

from __future__ import annotations

import json
from typing import Any

from ..models import Mode, NodeSpec
from ..runtime.agents import (
    AgentContext,
    AgentOutput,
    Review,
    ReviewContext,
    ReviewDecision,
    estimate_tokens,
)
from ..runtime.tools import ToolAction
from .normalize import coerce_actions, extract_json_object, normalize_confidence, normalize_decision

PROPOSE_SYSTEM = """你是 Narthex 多智能体编排运行时中的一个 Agent。
规则：
1. 只输出一个 JSON 对象，不要输出其他文字，不要使用 Markdown 代码块以外的解释。
2. JSON 字段：content（字符串，提案正文）、confidence（0 到 1 的数字）、
   actions（数组，元素为 {tool, args, reason}，可为空）、artifacts（对象，可为空）。
3. 使用与任务相同的语言撰写 content。
4. 不要声称执行了任何工具；actions 只是待审批的副作用请求。"""

REVIEW_SYSTEM_CATHEDRAL = """你是 Narthex Cathedral 审批通道中的审批 Agent。
规则：
1. 只输出一个 JSON 对象：decision、rationale、confidence。
2. decision 只能取：approve（批准）、deny（拒绝）、escalate（升级人工）。
3. rationale 用一句话说明理由，语言与任务一致。
4. confidence 是 0 到 1 的数字。
5. 若提案范围超出批准能力或缺少回滚依据，选择 deny 或 escalate，而不是含糊通过。"""

REVIEW_SYSTEM_BAZAAR = """你是 Narthex Bazaar 协商通道中的评审 Agent。
规则：
1. 只输出一个 JSON 对象：decision、rationale、confidence。
2. decision 只能取：endorse（认可）、challenge（质疑并要求修订）。
3. rationale 用一句话说明理由，语言与任务一致。
4. confidence 是 0 到 1 的数字。
5. 若提案缺少可验证性、风险边界或回滚依据，选择 challenge。"""

REVIEW_SYSTEMS = {
    Mode.CATHEDRAL: REVIEW_SYSTEM_CATHEDRAL,
    Mode.BAZAAR: REVIEW_SYSTEM_BAZAAR,
}

REVIEW_ALLOWED = {
    Mode.CATHEDRAL: ("approve", "deny", "escalate"),
    Mode.BAZAAR: ("endorse", "challenge"),
}


def _upstream_block(upstream: Any, limit: int = 400) -> str:
    lines: list[str] = []
    for node_id, view in list(dict(upstream or {}).items())[-6:]:
        summary = " ".join(str(view.get("output", "")).split())
        if len(summary) > limit:
            summary = summary[:limit] + "..."
        lines.append(f"- {node_id}: {summary or '(空)'}")
    return "\n".join(lines) or "- 无上游产物"


def _capabilities_block(capabilities: Any) -> str:
    return "、".join(sorted(capabilities)) if capabilities else "（无）"


def build_propose_prompt(context: AgentContext) -> str:
    node: NodeSpec = context.node
    return (
        f"## 任务\n{context.instruction}\n"
        f"\n## 角色与模式\n"
        f"agent_id: {context.agent.id}\n"
        f"role: {context.agent.role}\n"
        f"node_id: {node.id}\n"
        f"phase: {node.phase}\n"
        f"mode: {context.mode.value}\n"
        f"可用能力: {_capabilities_block(context.capabilities)}\n"
        f"\n## 上游产物\n{_upstream_block(context.upstream)}\n"
        f"\n## 协商轮次\n第 {context.round_index} 轮\n"
        "\n请基于以上信息生成提案，输出契约要求的 JSON。"
    )


def build_review_prompt(context: ReviewContext) -> str:
    proposal_summary = " ".join(context.proposal.content.split())
    if len(proposal_summary) > 800:
        proposal_summary = proposal_summary[:800] + "..."
    return (
        f"## 待评审提案\n{proposal_summary}\n"
        f"\n## 提案元信息\n"
        f"提案 agent: {context.proposal_agent_id}\n"
        f"node_id: {context.node.id}\n"
        f"phase: {context.node.phase}\n"
        f"mode: {context.mode.value}\n"
        f"提案 confidence: {context.proposal.confidence:.2f}\n"
        f"协商轮次: {context.round_index}\n"
        f"\n## 任务背景\n{context.task}\n"
        f"\n## 上游产物\n{_upstream_block(context.upstream)}\n"
        "\n请输出契约要求的 JSON。"
    )


def build_revise_prompt(
    context: AgentContext, output: AgentOutput, reviews: list[Review]
) -> str:
    review_lines = [
        f"- [{review.decision.value}] {review.rationale or '(无理由)'}"
        for review in reviews
    ]
    original = " ".join(output.content.split())
    if len(original) > 800:
        original = original[:800] + "..."
    return (
        f"## 原提案\n{original}\n"
        f"\n## 评审意见\n" + ("\n".join(review_lines) or "- 无") + "\n"
        f"\n## 任务\n{context.instruction}\n"
        "\n请吸收评审意见后输出修订提案，输出契约要求的 JSON。"
    )


class ProposeContract:
    """Parse a proposal answer into a canonical AgentOutput."""

    system = PROPOSE_SYSTEM

    def parse(
        self,
        text: str,
        context: AgentContext,
        *,
        fallback_content: str = "",
    ) -> AgentOutput:
        payload = extract_json_object(text)
        if payload is None:
            if fallback_content:
                return AgentOutput(
                    content=fallback_content,
                    confidence=0.8,
                    metadata={"contract_fallback": True},
                )
            raise ValueError("proposal response is not a JSON object")
        content = str(payload.get("content") or "").strip()
        if not content:
            if fallback_content:
                return AgentOutput(
                    content=fallback_content,
                    confidence=0.8,
                    metadata={"contract_fallback": True},
                )
            raise ValueError("proposal response has empty content")
        artifacts = payload.get("artifacts")
        metadata = dict(payload.get("metadata") or {})
        return AgentOutput(
            content=content,
            confidence=normalize_confidence(payload.get("confidence")),
            actions=[
                ToolAction(action["tool"], action["args"], action["reason"])
                for action in coerce_actions(payload.get("actions"))
            ],
            artifacts=dict(artifacts) if isinstance(artifacts, dict) else {},
            metadata=metadata,
        )


class ReviewContract:
    """Parse a review answer into a canonical Review."""

    systems = REVIEW_SYSTEMS
    allowed = REVIEW_ALLOWED

    def system_for(self, mode: Mode) -> str:
        return self.systems[mode]

    def parse(self, text: str, mode: Mode) -> Review:
        payload = extract_json_object(text)
        if payload is None:
            # Conservative: an unparsable verdict blocks progress rather
            # than silently endorsing it.
            decision = "escalate" if mode is Mode.CATHEDRAL else "challenge"
            return Review(
                decision=ReviewDecision.parse(decision),
                rationale="评审输出不符合契约，触发保守默认决策",
                confidence=0.3,
                token_cost=estimate_tokens(text or "unparsable"),
            )
        decision = normalize_decision(
            payload.get("decision"), allowed=self.allowed[mode]
        )
        rationale = str(payload.get("rationale") or payload.get("reason") or "").strip()
        return Review(
            decision=ReviewDecision.parse(decision),
            rationale=rationale,
            confidence=normalize_confidence(payload.get("confidence")),
            token_cost=max(1, estimate_tokens(rationale or "review")),
        )


def serialize_review(review: Review) -> str:
    return json.dumps(review.to_dict(), ensure_ascii=False)
