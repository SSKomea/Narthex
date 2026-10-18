"""Tool registry and capability-checked invocation."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from ..audit import AuditLog
from ..capabilities import capability_grants
from ..errors import ToolDenied, ToolNotFound
from ..events import EventKind
from ..models import Mode


@dataclass(frozen=True)
class ToolAction:
    """A proposed side effect that is committed only after a gate passes."""

    tool: str
    args: dict[str, Any] = field(default_factory=dict)
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"tool": self.tool, "args": dict(self.args), "reason": self.reason}


@dataclass(frozen=True)
class ToolCallContext:
    run_id: str
    node_id: str
    agent_id: str
    mode: Mode
    artifacts_dir: Path
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Tool:
    name: str
    required_capability: str
    handler: Callable[[Mapping[str, Any], ToolCallContext], Any]
    description: str = ""


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> "ToolRegistry":
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool: {tool.name!r}")
        self._tools[tool.name] = tool
        return self

    def add(
        self,
        name: str,
        required_capability: str,
        handler: Callable[[Mapping[str, Any], ToolCallContext], Any],
        description: str = "",
    ) -> "ToolRegistry":
        return self.register(Tool(name, required_capability, handler, description))

    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise ToolNotFound(f"unknown tool: {name!r}") from exc

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._tools))


class ToolInvoker:
    """Bound to one node execution and its current capability envelope."""

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        run_id: str,
        node_id: str,
        agent_id: str,
        mode: Mode,
        capabilities: frozenset[str],
        artifacts_dir: str | Path,
        audit: AuditLog | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.registry = registry
        self.run_id = run_id
        self.node_id = node_id
        self.agent_id = agent_id
        self.mode = mode
        self.capabilities = capabilities
        self.artifacts_dir = Path(artifacts_dir)
        self.audit = audit
        self.metadata = dict(metadata or {})

    def invoke(self, name: str, /, **args: Any) -> Any:
        try:
            tool = self.registry.get(name)
        except ToolNotFound:
            if self.audit is not None:
                self.audit.emit(
                    EventKind.TOOL_DENIED,
                    node_id=self.node_id,
                    agent_id=self.agent_id,
                    mode=self.mode,
                    tool=name,
                    required=None,
                    reason="unknown_tool",
                    capabilities=sorted(self.capabilities),
                )
            raise
        if not capability_grants(self.capabilities, tool.required_capability):
            if self.audit is not None:
                self.audit.emit(
                    EventKind.TOOL_DENIED,
                    node_id=self.node_id,
                    agent_id=self.agent_id,
                    mode=self.mode,
                    tool=name,
                    required=tool.required_capability,
                    reason="capability_missing",
                    capabilities=sorted(self.capabilities),
                )
            raise ToolDenied(
                f"agent {self.agent_id!r} lacks {tool.required_capability!r} "
                f"required by tool {name!r}"
            )
        context = ToolCallContext(
            run_id=self.run_id,
            node_id=self.node_id,
            agent_id=self.agent_id,
            mode=self.mode,
            artifacts_dir=self.artifacts_dir,
            metadata=self.metadata,
        )
        result = tool.handler(args, context)
        if self.audit is not None:
            self.audit.emit(
                EventKind.TOOL_CALL,
                node_id=self.node_id,
                agent_id=self.agent_id,
                mode=self.mode,
                tool=name,
                required=tool.required_capability,
                args=args,
                result=result,
            )
        return result


def _safe_artifact_path(artifacts_dir: Path, requested: str) -> Path:
    filename = Path(requested).name
    if not filename:
        raise ValueError("artifact path must include a file name")
    target = (artifacts_dir / filename).resolve()
    root = artifacts_dir.resolve()
    if root not in target.parents and target != root:
        raise ValueError("artifact path escapes the artifacts directory")
    return target


def build_demo_tools() -> ToolRegistry:
    """Tools used by examples and tests. Replace them in real applications."""

    registry = ToolRegistry()

    def note(args: Mapping[str, Any], ctx: ToolCallContext) -> dict[str, Any]:
        text = str(args.get("text", ""))
        return {"noted": True, "characters": len(text), "node": ctx.node_id}

    def read(args: Mapping[str, Any], ctx: ToolCallContext) -> dict[str, Any]:
        requested = str(args.get("path", ""))
        content = str(args.get("content", ""))
        if requested:
            target = _safe_artifact_path(ctx.artifacts_dir, requested)
            if target.exists():
                content = target.read_text(encoding="utf-8")
        return {
            "read": True,
            "path": requested,
            "content": content,
            "node": ctx.node_id,
        }

    def write(args: Mapping[str, Any], ctx: ToolCallContext) -> dict[str, Any]:
        target = _safe_artifact_path(ctx.artifacts_dir, str(args.get("path", "artifact.md")))
        target.parent.mkdir(parents=True, exist_ok=True)
        content = str(args.get("content", ""))
        target.write_text(content, encoding="utf-8")
        return {"written": True, "path": str(target), "bytes": len(content.encode("utf-8"))}

    registry.add("work.note", "propose", note, "Record an intermediate proposal")
    registry.add("work.read", "read", read, "Inspect a proposal or artifact")
    registry.add("work.write", "execute", write, "Write an approved artifact to disk")
    return registry
