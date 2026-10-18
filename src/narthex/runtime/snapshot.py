"""State snapshots and branch metadata for replay."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..errors import SnapshotError
from ..events import to_jsonable


_SAFE_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def safe_component(value: str) -> str:
    cleaned = _SAFE_RE.sub("-", value).strip("-.")
    return cleaned or "item"


@dataclass
class Snapshot:
    snapshot_id: str
    run_id: str
    created_at: str
    event_seq: int
    node_id: str
    reason: str
    mode: str
    branch_id: str | None
    parent_run_id: str | None
    task: str
    topology: dict[str, Any]
    state: dict[str, Any]
    next_nodes: list[str]
    path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return to_jsonable(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Snapshot":
        return cls(**data)

    def save(self, snapshot_dir: str | Path, sequence: int) -> Path:
        snapshot_dir = Path(snapshot_dir)
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        filename = (
            f"{sequence:04d}-{safe_component(self.node_id)}-"
            f"{safe_component(self.reason)}.json"
        )
        path = snapshot_dir / filename
        path.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        self.path = str(path)
        return path

    @classmethod
    def load(cls, path: str | Path) -> "Snapshot":
        path = Path(path)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            snapshot = cls.from_dict(data)
        except Exception as exc:
            raise SnapshotError(f"could not load snapshot {path}: {exc}") from exc
        snapshot.path = str(path)
        return snapshot


def create_snapshot(
    *,
    snapshot_id: str,
    run_id: str,
    event_seq: int,
    node_id: str,
    reason: str,
    mode: str,
    branch_id: str | None,
    parent_run_id: str | None,
    task: str,
    topology: dict[str, Any],
    state: dict[str, Any],
    next_nodes: list[str],
) -> Snapshot:
    return Snapshot(
        snapshot_id=snapshot_id,
        run_id=run_id,
        created_at=_utc_now(),
        event_seq=event_seq,
        node_id=node_id,
        reason=reason,
        mode=mode,
        branch_id=branch_id,
        parent_run_id=parent_run_id,
        task=task,
        topology=topology,
        state=state,
        next_nodes=next_nodes,
    )
