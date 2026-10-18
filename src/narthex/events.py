"""Unified audit event vocabulary and JSON conversion helpers."""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from enum import Enum
from pathlib import Path
from typing import Any, cast


class EventKind:
    RUN_STARTED = "run_started"
    RUN_COMPLETED = "run_completed"
    RUN_BLOCKED = "run_blocked"
    RUN_FAILED = "run_failed"
    REPLAY_STARTED = "replay_started"
    REPLAY_COMPLETED = "replay_completed"

    NODE_STARTED = "node_started"
    NODE_COMPLETED = "node_completed"
    NODE_BLOCKED = "node_blocked"
    NODE_FAILED = "node_failed"

    MODE_DECISION = "mode_decision"
    MODE_SWITCH = "mode_switch"
    POLICY_OVERRIDE = "policy_override"

    PROPOSE = "propose"
    REVISE = "revise"
    ENDORSE = "endorse"
    CHALLENGE = "challenge"
    CONSENSUS_REACHED = "consensus_reached"

    APPROVAL_REQUESTED = "approval_requested"
    APPROVE = "approve"
    DENY = "deny"
    ESCALATE = "escalate"
    APPROVAL_GRANTED = "approval_granted"

    TOOL_CALL = "tool_call"
    TOOL_DENIED = "tool_denied"
    ACTION_DEFERRED = "action_deferred"

    SNAPSHOT_CREATED = "snapshot_created"
    ARTIFACT_CREATED = "artifact_created"


def to_jsonable(value: Any) -> Any:
    """Recursively convert dataclasses, enums, paths and sets to JSON values."""
    if isinstance(value, Enum):
        return value.value
    if dataclasses.is_dataclass(value):
        return to_jsonable(dataclasses.asdict(cast(Any, value)))
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted(to_jsonable(item) for item in value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value
