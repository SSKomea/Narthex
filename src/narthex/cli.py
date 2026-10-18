"""Command line interface for Narthex."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .audit import audit_summary, verify_audit
from .models import Topology
from .runtime.agents import build_demo_registry
from .runtime.engine import NarthexRuntime, RunResult, RuntimeOptions
from .runtime.snapshot import Snapshot
from .runtime.tools import build_demo_tools
from .topology.loader import load_topology


DEFAULT_TASK = "为一套基础软件设计一个可审计、可回滚的多智能体变更流程"


def _write_json(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def _result_exit_code(result: RunResult) -> int:
    if result.status == "completed":
        return 0
    if result.status == "blocked":
        return 2
    return 1


def _print_result(result: RunResult) -> None:
    print(f"status      : {result.status}")
    print(f"run_id      : {result.run_id}")
    print(f"branch      : {result.branch_id or '-'}")
    print(f"parent      : {result.parent_run_id or '-'}")
    print(f"modes       : {result.mode_counts()}")
    print(f"tokens      : {result.total_tokens}")
    print(f"divergence  : {result.max_divergence:.3f}")
    print(f"run_dir     : {result.run_dir}")
    print(f"audit       : {result.audit_path}")
    print(f"snapshots   : {len(result.snapshot_paths)}")
    if result.error:
        print(f"error       : {result.error}")
    if result.final_output:
        print("\n--- final output ---")
        print(result.final_output)


def _build_runtime(
    topology: Topology,
    *,
    run_root: str,
    run_id: str | None,
    auto_approve: bool,
    approved_nodes: list[str],
    denied_nodes: list[str],
    max_rounds: int | None,
) -> NarthexRuntime:
    return NarthexRuntime(
        topology,
        build_demo_registry(topology),
        tools=build_demo_tools(),
        options=RuntimeOptions(
            run_root=run_root,
            run_id=run_id,
            auto_approve=auto_approve,
            approved_nodes=frozenset(approved_nodes),
            denied_nodes=frozenset(denied_nodes),
            max_rounds=max_rounds,
        ),
    )


def _cmd_run(args: argparse.Namespace) -> int:
    topology = load_topology(args.topology)
    runtime = _build_runtime(
        topology,
        run_root=args.run_root,
        run_id=args.run_id,
        auto_approve=args.auto_approve,
        approved_nodes=args.approve_node,
        denied_nodes=args.deny_node,
        max_rounds=args.max_rounds,
    )
    result = runtime.run(args.task)
    if args.json:
        _write_json(result.to_dict())
    else:
        _print_result(result)
    return _result_exit_code(result)


def _cmd_validate(args: argparse.Namespace) -> int:
    topology = load_topology(args.topology)
    order = topology.order()
    data = {
        "name": topology.name,
        "default_mode": topology.default_mode.value,
        "agents": sorted(topology.agents),
        "nodes": list(order),
        "edges": [edge.to_dict() for edge in topology.edges],
        "governor": topology.governor.to_dict(),
    }
    if args.json:
        _write_json(data)
    else:
        print(f"OK: {topology.name}")
        print(f"agents : {len(topology.agents)}")
        print(f"nodes  : {len(topology.nodes)}")
        print(f"order  : {' -> '.join(order)}")
    return 0


def _cmd_inspect(args: argparse.Namespace) -> int:
    target = Path(args.path)
    if target.is_dir():
        summary_path = target / "summary.json"
        audit_path = target / "audit.jsonl"
        data: dict[str, Any] = {}
        if summary_path.exists():
            data["summary"] = json.loads(summary_path.read_text(encoding="utf-8"))
        if audit_path.exists():
            data["audit"] = audit_summary(audit_path)
        if not data:
            print(f"no summary.json or audit.jsonl in {target}", file=sys.stderr)
            return 1
    elif target.name == "audit.jsonl" or target.suffix == ".jsonl":
        data = {"audit": audit_summary(target)}
    elif target.suffix == ".json":
        data = {"summary": json.loads(target.read_text(encoding="utf-8"))}
    else:
        print(f"unsupported inspect target: {target}", file=sys.stderr)
        return 1
    _write_json(data)
    return 0


def _cmd_replay(args: argparse.Namespace) -> int:
    snapshot = Snapshot.load(args.snapshot)
    topology = Topology.from_dict(snapshot.topology)
    runtime = _build_runtime(
        topology,
        run_root=args.run_root,
        run_id=args.run_id,
        auto_approve=args.auto_approve,
        approved_nodes=args.approve_node,
        denied_nodes=args.deny_node,
        max_rounds=args.max_rounds,
    )
    result = runtime.replay(
        snapshot,
        from_node=args.from_node,
        task=args.task,
    )
    if args.json:
        _write_json(result.to_dict())
    else:
        _print_result(result)
    return _result_exit_code(result)


def _cmd_verify(args: argparse.Namespace) -> int:
    ok, message = verify_audit(args.audit)
    print(("OK: " if ok else "FAILED: ") + message)
    return 0 if ok else 1


def _cmd_demo(args: argparse.Namespace) -> int:
    root = Path(__file__).resolve().parents[2]
    source_example = root / "examples" / "mixed_delivery.yaml"
    packaged_example = Path(__file__).resolve().parent / "data" / "mixed_delivery.yaml"
    topology_path = source_example if source_example.exists() else packaged_example
    topology = load_topology(topology_path)
    runtime = _build_runtime(
        topology,
        run_root=args.run_root,
        run_id=args.run_id,
        auto_approve=True,
        approved_nodes=[],
        denied_nodes=[],
        max_rounds=None,
    )
    result = runtime.run(args.task)
    if args.json:
        _write_json(result.to_dict())
    else:
        _print_result(result)
    return _result_exit_code(result)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="narthex",
        description="Cathedral/Bazaar 可切换的多智能体编排运行时",
    )
    parser.add_argument("--version", action="version", version=f"narthex {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run", help="运行一个拓扑")
    run.add_argument("topology", help="YAML/JSON/Python DSL 拓扑文件")
    run.add_argument("--task", default=DEFAULT_TASK)
    run.add_argument("--run-root", default="runs")
    run.add_argument("--run-id")
    run.add_argument("--no-auto-approve", dest="auto_approve", action="store_false")
    run.set_defaults(auto_approve=True)
    run.add_argument("--approve-node", action="append", default=[])
    run.add_argument("--deny-node", action="append", default=[])
    run.add_argument("--max-rounds", type=int)
    run.add_argument("--json", action="store_true")
    run.set_defaults(func=_cmd_run)

    validate = subparsers.add_parser("validate", help="校验拓扑")
    validate.add_argument("topology")
    validate.add_argument("--json", action="store_true")
    validate.set_defaults(func=_cmd_validate)

    inspect = subparsers.add_parser("inspect", help="查看运行目录或审计日志")
    inspect.add_argument("path")
    inspect.set_defaults(func=_cmd_inspect)

    replay = subparsers.add_parser("replay", help="从快照创建分支并继续执行")
    replay.add_argument("snapshot")
    replay.add_argument("--from-node")
    replay.add_argument("--task")
    replay.add_argument("--run-root", default="runs")
    replay.add_argument("--run-id")
    replay.add_argument("--no-auto-approve", dest="auto_approve", action="store_false")
    replay.set_defaults(auto_approve=True)
    replay.add_argument("--approve-node", action="append", default=[])
    replay.add_argument("--deny-node", action="append", default=[])
    replay.add_argument("--max-rounds", type=int)
    replay.add_argument("--json", action="store_true")
    replay.set_defaults(func=_cmd_replay)

    verify = subparsers.add_parser("verify", help="校验审计哈希链")
    verify.add_argument("audit")
    verify.set_defaults(func=_cmd_verify)

    demo = subparsers.add_parser("demo", help="运行内置混合拓扑示例")
    demo.add_argument("--task", default=DEFAULT_TASK)
    demo.add_argument("--run-root", default="runs")
    demo.add_argument("--run-id")
    demo.add_argument("--json", action="store_true")
    demo.set_defaults(func=_cmd_demo)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
