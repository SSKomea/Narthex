"""跨大模型一致性示例：把任意 OpenAI 兼容后端接入 Narthex。

同一个拓扑、同一套契约，可无缝切换 DeepSeek / Qwen / Ollama / vLLM / OpenAI。
运行方式：

    # 离线确定性后端（无需 API Key）
    python examples/llm_agents.py

    # 指定真实模型
    set NARTHEX_LLM_MODEL=deepseek-chat
    set NARTHEX_LLM_BASE_URL=https://api.deepseek.com/v1
    set NARTHEX_LLM_API_KEY=sk-...
    python examples/llm_agents.py

    # 本地 Ollama
    set NARTHEX_LLM_MODEL=qwen2.5:7b
    set NARTHEX_LLM_BASE_URL=http://localhost:11434/v1
    python examples/llm_agents.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from narthex import (  # noqa: E402
    NarthexRuntime,
    RuntimeOptions,
    build_demo_tools,
    build_llm_registry,
    load_topology,
)


def main() -> None:
    topology = load_topology(Path(__file__).with_name("mixed_delivery.yaml"))

    # build_llm_registry 内部默认调用 provider_from_env()：
    # 未设置 NARTHEX_LLM_MODEL 时退回离线确定性后端，保证示例永远可跑。
    registry = build_llm_registry(topology)

    with tempfile.TemporaryDirectory() as run_root:
        runtime = NarthexRuntime(
            topology,
            registry,
            tools=build_demo_tools(),
            options=RuntimeOptions(run_root=run_root),
        )
        result = runtime.run("设计一个可审计的发布流程")
        print("status:", result.status)
        print("mode_counts:", result.mode_counts())
        print("total_tokens(模型无关口径):", result.to_dict().get("total_tokens"))


if __name__ == "__main__":
    main()
