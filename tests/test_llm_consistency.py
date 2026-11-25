"""跨大模型一致性测试：不同后端的输出被归一到相同语义。"""

from __future__ import annotations

import tempfile
import unittest

import _bootstrap  # noqa: F401

from narthex import (
    AgentContext,
    AgentOutput,
    AgentSpec,
    CachedProvider,
    DeterministicProvider,
    LLMAgent,
    Mode,
    NodeSpec,
    OpenAICompatProvider,
    Review,
    ReviewContext,
    ReviewDecision,
    ScriptedProvider,
    build_demo_registry,
    build_demo_tools,
    build_llm_registry,
    load_topology,
    NarthexRuntime,
    RuntimeOptions,
    normalize_confidence,
    normalize_decision,
)
from narthex.llm.provider import LLMProviderError, provider_from_env
from narthex.runtime.agents import estimate_tokens


def _node(node_id: str = "design") -> NodeSpec:
    return NodeSpec(id=node_id, agent="engineer", phase="design", task="设计实施方案")


def _agent_spec(agent_id: str = "engineer") -> AgentSpec:
    return AgentSpec(id=agent_id, role="engineer", capabilities=frozenset({"propose"}))


def _agent_context(mode: Mode = Mode.BAZAAR) -> AgentContext:
    return AgentContext(
        run_id="run-1",
        topology_name="t",
        node=_node(),
        agent=_agent_spec(),
        task="设计实施方案",
        mode=mode,
        capabilities=frozenset({"propose"}),
        tools=None,
    )


def _review_context(
    mode: Mode = Mode.BAZAAR,
    proposal: AgentOutput | None = None,
    agent_id: str = "reviewer",
) -> ReviewContext:
    return ReviewContext(
        run_id="run-1",
        topology_name="t",
        node=_node(),
        agent=_agent_spec(agent_id),
        mode=mode,
        proposal=proposal or AgentOutput(content="模块化方案，保留审计与回放。"),
        proposal_agent_id="engineer",
        task="设计实施方案",
    )


class DecisionNormalizationTests(unittest.TestCase):
    def test_bilingual_synonyms_map_to_canonical(self) -> None:
        # 不同模型对同一判断的措辞差异，不应改变治理语义。
        samples = {
            "approve": ("approve", "APPROVE", "批准", "通过", "LGTM", "审核通过"),
            "deny": ("deny", "否决", "拒绝", "REJECTED", "veto"),
            "escalate": ("escalate", "人工审批", "升级", "无法判断"),
            "endorse": ("endorse", "认可", "同意", "支持", "无异议"),
            "challenge": ("challenge", "质疑", "需修改", "异议"),
        }
        for canonical, variants in samples.items():
            for variant in variants:
                self.assertEqual(
                    normalize_decision(variant, allowed=tuple(samples)),
                    canonical,
                    msg=f"{variant!r} should map to {canonical}",
                )

    def test_unparsable_decision_is_conservative(self) -> None:
        allowed = ("endorse", "challenge")
        self.assertEqual(
            normalize_decision("完全看不懂的回答", allowed=allowed), "challenge"
        )
        self.assertEqual(
            normalize_decision("", allowed=("approve", "deny", "escalate")),
            "escalate",
        )

    def test_confidence_scales_are_normalized(self) -> None:
        self.assertAlmostEqual(normalize_confidence(85), 0.85)
        self.assertAlmostEqual(normalize_confidence(8), 0.8)
        self.assertAlmostEqual(normalize_confidence("high"), 0.9)
        self.assertAlmostEqual(normalize_confidence("中"), 0.7)
        self.assertAlmostEqual(normalize_confidence(1.0), 1.0)
        self.assertAlmostEqual(normalize_confidence(-2), 0.0)
        self.assertAlmostEqual(normalize_confidence(None), 0.8)


class ContractParsingTests(unittest.TestCase):
    def test_markdown_fenced_json_is_parsed(self) -> None:
        from narthex.llm.contracts import ProposeContract

        text = (
            "好的，以下是我的提案：\n```json\n"
            '{"content": "模块化方案", "confidence": 0.9, "actions": []}\n'
            "```\n以上。"
        )
        output = ProposeContract().parse(text, _agent_context())
        self.assertEqual(output.content, "模块化方案")
        self.assertAlmostEqual(output.confidence, 0.9)

    def test_full_width_quotes_tolerated(self) -> None:
        from narthex.llm.contracts import ProposeContract

        text = '{"content": “方案正文”, "confidence": 90}'
        output = ProposeContract().parse(text, _agent_context())
        self.assertEqual(output.content, "方案正文")
        self.assertAlmostEqual(output.confidence, 0.9)

    def test_malformed_actions_are_dropped(self) -> None:
        from narthex.llm.contracts import ProposeContract

        text = (
            '{"content": "方案", "confidence": 0.7, '
            '"actions": [{"tool": "work.note", "args": {"text": "x"}}, '
            '{"args": {}}, "junk"]}'
        )
        output = ProposeContract().parse(text, _agent_context())
        self.assertEqual(len(output.actions), 1)
        self.assertEqual(output.actions[0].tool, "work.note")

    def test_review_fallback_is_conservative(self) -> None:
        from narthex.llm.contracts import ReviewContract

        contract = ReviewContract()
        bazaar = contract.parse("这不是 JSON", Mode.BAZAAR)
        self.assertIs(bazaar.decision, ReviewDecision.CHALLENGE)
        cathedral = contract.parse("也不是 JSON", Mode.CATHEDRAL)
        self.assertIs(cathedral.decision, ReviewDecision.ESCALATE)


class LLMAgentConsistencyTests(unittest.TestCase):
    def test_three_model_styles_yield_same_decision(self) -> None:
        # 模拟三种模型风格：严格 JSON、包在散文里、中英混排 + 大写。
        styles = {
            "strict": '{"decision": "endorse", "rationale": "可行", "confidence": 0.8}',
            "verbose": (
                "根据我的分析：\n\n```json\n"
                '{"decision": "ENDORSE", "rationale": "可行", "confidence": "high"}\n'
                "```"
            ),
            "chatty": '我的结论是 {"decision": "认可", "rationale": "方案可行", '
            '"confidence": 80}，希望有帮助。',
        }
        from narthex.llm.contracts import ReviewContract

        contract = ReviewContract()
        decisions = set()
        for text in styles.values():
            review = contract.parse(text, Mode.BAZAAR)
            decisions.add(review.decision)
        self.assertEqual(len(decisions), 1)
        self.assertIn(ReviewDecision.ENDORSE, decisions)

    def test_contract_breakage_triggers_repair_then_fallback(self) -> None:
        provider = ScriptedProvider(
            ["抱歉，我无法输出 JSON", "当然可以！随便说点啥", "还是不是 JSON"]
        )
        agent = LLMAgent(_agent_spec(), provider, repair_rounds=1)
        output = agent.propose(_agent_context())
        # 修复轮失败后回退到确定性输出，运行不会中断。
        self.assertTrue(output.metadata.get("llm_fallback"))
        self.assertIn("设计整合", output.content)
        self.assertEqual(len(provider.calls), 2)

    def test_token_cost_is_model_agnostic(self) -> None:
        # 即使 provider 报告了 usage，预算口径仍然使用 estimate_tokens。
        provider = ScriptedProvider(
            ['{"content": "短方案", "confidence": 0.9, "actions": []}']
        )
        agent = LLMAgent(_agent_spec(), provider)
        output = agent.propose(_agent_context())
        self.assertEqual(output.token_cost, estimate_tokens("短方案"))
        self.assertGreater(output.token_cost, 0)

    def test_review_provider_failure_falls_back_deterministically(self) -> None:
        provider = ScriptedProvider([LLMProviderError("network down")])
        agent = LLMAgent(_agent_spec("reviewer"), provider)
        review = agent.review(_review_context())
        self.assertIs(review.decision, ReviewDecision.ENDORSE)
        self.assertIn("确定性兜底", review.rationale)


class ProviderTests(unittest.TestCase):
    def test_openai_payload_shape(self) -> None:
        payload = OpenAICompatProvider.build_payload(
            "deepseek-chat",
            "system prompt",
            "user prompt",
            max_tokens=512,
            temperature=0.2,
        )
        self.assertEqual(payload["model"], "deepseek-chat")
        self.assertEqual(payload["messages"][0]["role"], "system")
        self.assertEqual(payload["messages"][1]["content"], "user prompt")
        self.assertEqual(payload["max_tokens"], 512)
        self.assertFalse(payload["stream"])

    def test_openai_response_parsing(self) -> None:
        text, usage = OpenAICompatProvider.parse_response(
            {
                "choices": [{"message": {"content": "hello"}}],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                },
            }
        )
        self.assertEqual(text, "hello")
        self.assertEqual(usage, {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})

    def test_provider_from_env_offline_default(self) -> None:
        provider = provider_from_env({})
        self.assertIsInstance(provider, DeterministicProvider)
        provider = provider_from_env(
            {
                "NARTHEX_LLM_MODEL": "qwen2.5:7b",
                "NARTHEX_LLM_BASE_URL": "http://localhost:11434/v1",
            }
        )
        self.assertIsInstance(provider, OpenAICompatProvider)
        self.assertEqual(provider.base_url, "http://localhost:11434/v1")

    def test_cached_provider_dedupes_identical_calls(self) -> None:
        inner = DeterministicProvider()
        cached = CachedProvider(inner)
        first = cached.complete("same prompt", system="same system")
        second = cached.complete("same prompt", system="same system")
        self.assertIs(first, second)
        self.assertEqual(cached.hits, 1)

    def test_deterministic_provider_always_valid_json(self) -> None:
        provider = DeterministicProvider()
        response = provider.complete("## 任务\n设计实施方案\n")
        self.assertIn("content", response.text)
        agent = LLMAgent(_agent_spec(), provider)
        output = agent.propose(_agent_context())
        self.assertFalse(output.metadata.get("llm_fallback"))
        self.assertTrue(output.content)


class RegistryIntegrationTests(unittest.TestCase):
    def test_build_llm_registry_covers_all_topology_agents(self) -> None:
        topology = load_topology("examples/mixed_delivery.yaml")
        registry = build_llm_registry(topology, DeterministicProvider(), cache=False)
        self.assertEqual(set(registry.ids()), set(topology.agents))

    def test_full_run_with_llm_agents_matches_demo_governance(self) -> None:
        # 关键一致性断言：LLM Agent 与 DemoAgent 在同一拓扑上产生相同的
        # 治理模式分布（Governor 决策不受后端影响）。
        topology = load_topology("examples/mixed_delivery.yaml")

        with tempfile.TemporaryDirectory() as left, tempfile.TemporaryDirectory() as right:
            demo_runtime = NarthexRuntime(
                topology,
                build_demo_registry(topology),
                tools=build_demo_tools(),
                options=RuntimeOptions(run_root=left),
            )
            demo_result = demo_runtime.run("设计一个可审计的发布流程")

            llm_registry = build_llm_registry(
                topology, DeterministicProvider(), cache=False
            )
            llm_runtime = NarthexRuntime(
                topology,
                llm_registry,
                tools=build_demo_tools(),
                options=RuntimeOptions(run_root=right),
            )
            llm_result = llm_runtime.run("设计一个可审计的发布流程")

        self.assertEqual(demo_result.mode_counts(), llm_result.mode_counts())
        self.assertEqual(demo_result.status, llm_result.status)


if __name__ == "__main__":
    unittest.main()
