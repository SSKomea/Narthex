# Narthex

Narthex 是一个 MIT 许可的、可在运行时切换治理范式的多智能体编排运行时。它把《大教堂与市集》中的两种软件治理哲学落成两个可执行的拓扑模式：

- **Cathedral**：有根、单向、强审批、强审计、可回放。
- **Bazaar**：去中心化协商、双向评审、阈值收敛、允许在探索阶段快速暴露分歧。

同一个 DAG 可以前面的节点运行在 Bazaar，提交生产变更时自动冻结为 Cathedral 审批链，形成混合编排。

## 许可说明

Cathedral 与 Bazaar 在这里指代两种软件治理与编排范式，而不是 SPDX 标准许可证，也不构成额外法律条款。Narthex 代码库本身以 MIT 许可证发布，允许闭源集成、商业分发和私有修改；运行时通过能力边界实现“许可宽松、权限可紧”。

## 快速开始

本项目使用 **Python 3.12**，没有第三方运行时依赖。

```powershell
cd Code

# 1. 校验示例拓扑
python3.12 run.py validate examples/mixed_delivery.yaml

# 2. 运行 Bazaar -> Cathedral 混合示例
python3.12 run.py demo --run-root .narthex-runs

# 3. 查看运行摘要与审计哈希链
python3.12 run.py inspect .narthex-runs\<run_id>
python3.12 run.py verify .narthex-runs\<run_id>\audit.jsonl

# 4. 从审批门禁快照创建分支并回放
python3.12 run.py replay .narthex-runs\<run_id>\snapshots\<snapshot>.json
```

### 安装到虚拟环境

```powershell
cd Code
python3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1

# 有网络时
python -m pip install -e .

# 离线环境（使用仓库内的 setup.py 兼容入口）
python setup.py develop

narthex demo
```

> 项目没有第三方运行时依赖。`.venv` 已用于 Python 3.12.13 验证；如果环境中没有 `python3.12` 命令，可以使用本机实际路径。

## 设计文档到代码的映射

| 《工具设计.docx》中的能力 | 实现位置 |
| --- | --- |
| 声明式拓扑、YAML / Python DSL | `src/narthex/topology/`、`examples/*.yaml`、`examples/dsl_topology.py` |
| `consult` 双向协商 / `authorize` 单向审批 | `models.EdgeType`、`runtime/engine.py` |
| Topology Governor 运行时切换 | `governance/governor.py` |
| 风险标签、阶段冻结、成本与分歧阈值 | `models.GovernorConfig`、`governance/governor.py` |
| `propose / endorse / challenge` | `events.py`、`runtime/engine.py::_run_bazaar` |
| `approve / deny / escalate` | `events.py`、`runtime/engine.py::_run_cathedral` |
| 统一审计流 | `audit.py`，JSONL + SHA-256 哈希链 |
| 状态快照与分支回放 | `runtime/snapshot.py`、`NarthexRuntime.replay` |
| 最小权限能力边界 | `capabilities.py`、`runtime/tools.py` |
| MIT 宽松许可 | `LICENSE` |

## 两种模式如何工作

### Cathedral

当一个节点满足以下任一条件时，Governor 会选择 Cathedral：

1. `phase` 属于 `commit / production / deploy / release / execute`。
2. 存在入站 `authorize` 边。
3. `risk` 命中 `high_risk_tags`。
4. Bazaar 累计 Token 或分歧度超过阈值。
5. Bazaar 有限轮次内没有形成共识。
6. 节点显式声明 `mode: cathedral`。

Cathedral 节点会：

1. 先生成提案（只拥有 `propose / consult / review / read` 类能力）。
2. 在审批门前写入状态快照。
3. 让配置的 `approvers` 和 `ApprovalProvider` 依次审批。
4. 只有通过后，才按 `approval.scope` 收窄后的能力集执行 `work.write` 等副作用。
5. 将 `approve / deny / escalate` 写入统一审计流。

### Bazaar

Bazaar 节点会：

1. 生成提案。
2. 让所有 `consult` 入边的源 Agent 进行评审，同时让当前 Agent 反向评审上游产物。
3. 计算认可率与文本分歧度。
4. 未收敛时在有限轮次内 `revise`。
5. 达到阈值即收敛；超过 Token/分歧阈值或轮次耗尽时，自动降级为 Cathedral 仲裁。
6. 如果 Bazaar 提案包含需要 `execute` 能力的动作，节点会自动冻结到 Cathedral 审批门；批准前不会发生任何副作用。

## 拓扑示例

```yaml
name: mixed-delivery
default_mode: bazaar
governor:
  high_risk_tags: [risk:high, risk:critical, risk:safety]
  commit_phases: [commit, production, deploy, release, execute]
  token_budget: 5000
  divergence_threshold: 0.92
  max_rounds: 3
  consensus_threshold: 0.6
agents:
  engineer:
    role: engineer
    capabilities: [propose, consult, execute, read]
  reviewer:
    role: verifier
    capabilities: [propose, approve, read]
nodes:
  - id: design
    agent: engineer
    phase: design
    risk: [risk:exploratory]
    task: 形成实施方案
  - id: production_change
    agent: engineer
    phase: commit
    risk: [risk:high]
    task: 提交生产变更
    capabilities: [propose, execute]
    approval:
      approvers: [reviewer]
      scope: [propose, execute]
edges:
  - from: design
    to: production_change
    type: authorize
```

YAML 解析器实现的是安全的 YAML 子集：嵌套映射、列表、标量、内联列表、引号与块字符串。项目不会执行 YAML 中的任意 Python 标签。`.py` 拓扑会作为 Python 模块执行，因此只应加载可信文件。

## CLI

```text
narthex run <topology>       运行拓扑
narthex validate <topology>  校验拓扑、DAG 与能力声明
narthex inspect <path>       查看 summary.json / audit.jsonl
narthex replay <snapshot>    从快照创建分支并继续执行
narthex verify <audit>       校验审计哈希链
narthex demo                 运行内置混合拓扑
```

常用参数：

```powershell
python3.12 run.py run examples/mixed_delivery.yaml `
  --task "设计一个可回放的发布流程" `
  --run-root .narthex-runs `
  --no-auto-approve `
  --approve-node production_change
```

`--no-auto-approve` 时，未列入 `--approve-node` 的 Cathedral 节点会升级为 `escalate`；可以用 `--deny-node` 演示审批阻断与分支回放。

## 编程接口

```python
from narthex import (
    NarthexRuntime,
    RuntimeOptions,
    build_demo_registry,
    build_demo_tools,
    load_topology,
)

topology = load_topology("examples/mixed_delivery.yaml")
runtime = NarthexRuntime(
    topology,
    build_demo_registry(topology),
    tools=build_demo_tools(),
    options=RuntimeOptions(run_root="runs"),
)
result = runtime.run("设计一个可审计的变更流程")
print(result.status, result.mode_counts())
```

替换为真实 Agent 时，实现 `propose`、`review` 和 `revise` 即可；`examples/custom_agents.py` 展示了 `FunctionAgent` 的用法。

```python
from narthex import AgentRegistry, FunctionAgent, AgentOutput

registry = AgentRegistry()
registry.register(FunctionAgent(spec, propose_fn, review_fn))
```

## 能力边界

能力是字符串，工具声明自己需要的唯一能力：

| 能力 | 典型用途 | Cathedral 提案阶段 | Cathedral 批准后 | Bazaar |
| --- | --- | --- | --- | --- |
| `propose` | 生成提案、记录中间产物 | 允许 | 允许 | 允许 |
| `consult` | 协商与评审 | 允许 | 允许 | 允许 |
| `read` | 读取上下文/产物 | 允许 | 允许（若在 scope 内） | 允许 |
| `approve` | 审批门禁 | 不用于提案 | 由 approver 使用 | 不授予 |
| `execute` | 写入、部署、外部副作用 | 不授予 | 仅在 `approval.scope` 内 | 不授予 |
| `execute.*` | 执行类通配能力 | 不授予 | 仅在 scope 内 | 不授予 |

工具在执行时仍会再次检查能力，因此即使 Agent 伪造 `ToolAction`，也无法绕过审批边界。若省略 `approval.scope`，Cathedral 只授予提案侧能力；需要执行时必须显式把 `execute` 或对应工具能力写入 scope。

## 审计与回放

审计流是 `runs/<run_id>/audit.jsonl`，每条事件包含：

- `seq`、时间戳、`run_id`、`node_id`、`agent_id`、`mode`
- 事件负载
- `prev_hash` 与 `hash`（SHA-256 链）

`narthex verify` 会检查序号、内容哈希和前序哈希。生产环境如果需要不可抵赖性，应在此基础上增加外部签名或 WORM 存储；哈希链本身不等于数字签名。

快照会在以下位置生成：

- 模式切换点
- 每个 Cathedral 审批门前
- 审批阻断点
- 节点失败点

快照保存拓扑、任务、已完成节点结果、Token、分歧度和剩余节点。`replay` 会保留分支点之前的结果，只重新执行该节点及其后续节点，并生成新的 `branch_id` 与 `parent_run_id`。

## 测试

```powershell
cd Code
python3.12 -m unittest discover -s tests -v
```

测试覆盖：YAML 子集、DAG 校验、Governor 规则、能力边界、工具拒绝、哈希链篡改检测、混合运行、审批阻断、Bazaar 提案自动冻结、快照分支回放和 CLI。

## 目录结构

```text
Code/
  run.py                  零安装启动入口
  setup.py                离线兼容安装入口
  src/narthex/
    audit.py              统一审计流
    capabilities.py       最小权限能力边界
    events.py             事件类型
    models.py             拓扑与 Governorship 数据模型
    cli.py                命令行
    governance/           Governor 与共识度量
    runtime/              Agent、工具、审批、执行、快照、回放
    topology/             YAML 子集、JSON、Python DSL
  examples/
    mixed_delivery.yaml   Bazaar -> Cathedral
    high_risk.yaml        全 Cathedral
    dsl_topology.py       Python DSL
    custom_agents.py      自定义 FunctionAgent
  tests/
  docs/architecture.md
```

## 限制与下一步

- 演示 Agent 是确定性的本地占位实现，不调用 LLM；接入真实模型时替换 Agent 的 `propose/review/revise` 即可。
- 文本分歧度使用 Jaccard 距离，适合演示和可解释基线；生产环境可替换为 embedding 距离或独立评审 Agent。
- 当前回放是“重新执行分支点及后续节点”，不会序列化任意 Python 对象；自定义 Agent 需要保持确定性或自行接入外部状态存储。
- 审批 Provider 当前提供自动/录制实现；接入人工审批时实现 `ApprovalProvider.decide` 即可。

## License

MIT，见 `LICENSE`。
