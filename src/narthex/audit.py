"""Tamper-evident append-only audit stream."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .events import to_jsonable

GENESIS_HASH = "0" * 64


def _canonical(data: Any) -> str:
    return json.dumps(
        data,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


@dataclass(frozen=True)
class AuditEvent:
    seq: int
    timestamp: str
    run_id: str
    kind: str
    node_id: str | None
    agent_id: str | None
    mode: str | None
    payload: dict[str, Any]
    prev_hash: str
    hash: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "timestamp": self.timestamp,
            "run_id": self.run_id,
            "kind": self.kind,
            "node_id": self.node_id,
            "agent_id": self.agent_id,
            "mode": self.mode,
            "payload": self.payload,
            "prev_hash": self.prev_hash,
            "hash": self.hash,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AuditEvent":
        return cls(**data)


def _event_digest(data: dict[str, Any]) -> str:
    unsigned = {key: value for key, value in data.items() if key != "hash"}
    return hashlib.sha256(_canonical(unsigned).encode("utf-8")).hexdigest()


class AuditLog:
    """Writes one hash-chained JSON object per line."""

    def __init__(self, path: str | Path, run_id: str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id
        self.seq = 0
        self.prev_hash = GENESIS_HASH
        self._file = self.path.open("a", encoding="utf-8", newline="\n")

    def emit(
        self,
        kind: str,
        *,
        node_id: str | None = None,
        agent_id: str | None = None,
        mode: Any = None,
        **payload: Any,
    ) -> AuditEvent:
        self.seq += 1
        event_data = {
            "seq": self.seq,
            "timestamp": _utc_now(),
            "run_id": self.run_id,
            "kind": kind,
            "node_id": node_id,
            "agent_id": agent_id,
            "mode": None if mode is None else getattr(mode, "value", str(mode)),
            "payload": to_jsonable(payload),
            "prev_hash": self.prev_hash,
        }
        digest = _event_digest(event_data)
        event_data["hash"] = digest
        self._file.write(_canonical(event_data) + "\n")
        self._file.flush()
        self.prev_hash = digest
        return AuditEvent.from_dict(event_data)

    def close(self) -> None:
        if not self._file.closed:
            self._file.close()

    def __enter__(self) -> "AuditLog":
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()


def read_events(path: str | Path) -> list[AuditEvent]:
    events: list[AuditEvent] = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                events.append(AuditEvent.from_dict(json.loads(line)))
            except Exception as exc:
                raise ValueError(f"invalid audit line {line_number}: {exc}") from exc
    return events


def verify_audit(path: str | Path) -> tuple[bool, str]:
    """Verify sequence numbers, hashes, and the previous-hash chain."""
    expected_prev = GENESIS_HASH
    events = read_events(path)
    if not events:
        return False, "audit log is empty"
    for index, event in enumerate(events, start=1):
        if event.seq != index:
            return False, f"event {index}: expected seq {index}, got {event.seq}"
        if event.prev_hash != expected_prev:
            return False, f"event {index}: previous hash mismatch"
        expected_hash = _event_digest(event.to_dict())
        if event.hash != expected_hash:
            return False, f"event {index}: content hash mismatch"
        expected_prev = event.hash
    return True, f"verified {len(events)} events"


def audit_summary(path: str | Path) -> dict[str, Any]:
    events = read_events(path)
    counts: dict[str, int] = {}
    for event in events:
        counts[event.kind] = counts.get(event.kind, 0) + 1
    return {
        "path": str(path),
        "events": len(events),
        "kinds": counts,
        "last_hash": events[-1].hash if events else None,
        "verified": verify_audit(path)[0] if events else False,
    }
