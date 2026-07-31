"""Closed-catalog predicate validation and evaluation."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any

from .db import as_utc
from .fleet_idle import FleetIdleSnapshot, eval_fleet_idle
from .models import (
    PREDICATE_FLEET_IDLE,
    PREDICATE_TRIGGER_TERMINAL,
    TriggerStoreError,
)

_REASON_UNKNOWN_PREDICATE = "unknown_predicate_type"
_REASON_EXPIRES_REQUIRED = "expires_at_required"
_REASON_UNRESOLVABLE_UPSTREAM = "unresolvable_upstream_trigger_id"
_REASON_MALFORMED_ARGS = "malformed_predicate_args"
_REASON_RECUR_INVALID = "recur_every_s_invalid"


def _parse_predicate_args(raw: str | dict[str, Any] | None) -> dict[str, Any]:
    if raw is None:
        raise TriggerStoreError(
            "predicate_args required when predicate is set",
            code=_REASON_MALFORMED_ARGS,
        )
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise TriggerStoreError(
            "predicate_args must be a JSON object",
            code=_REASON_MALFORMED_ARGS,
        ) from exc
    if not isinstance(parsed, dict):
        raise TriggerStoreError(
            "predicate_args must be a JSON object",
            code=_REASON_MALFORMED_ARGS,
        )
    return parsed


def _validate_fleet_idle_args(args: dict[str, Any]) -> None:
    for key in ("require_tick_empty", "require_dispatch_idle", "block_on_queued_consults"):
        if key in args and not isinstance(args[key], bool):
            raise TriggerStoreError(
                f"predicate_args.{key} must be a boolean",
                code=_REASON_MALFORMED_ARGS,
            )
    if "grace_s" in args:
        grace = args["grace_s"]
        if not isinstance(grace, int) or isinstance(grace, bool) or grace < 0:
            raise TriggerStoreError(
                "predicate_args.grace_s must be a non-negative integer",
                code=_REASON_MALFORMED_ARGS,
            )


def validate_predicate_schedule(
    conn: sqlite3.Connection,
    *,
    predicate: str | None,
    predicate_args: str | dict[str, Any] | None,
    expires_at: datetime | None,
    recur_every_s: int | None = None,
) -> tuple[str | None, str | None, str | None]:
    """Return (predicate, predicate_args_json, expires_at_iso) or raise."""
    if predicate is None:
        if predicate_args is not None or expires_at is not None:
            raise TriggerStoreError(
                "predicate_args and expires_at require predicate",
                code=_REASON_MALFORMED_ARGS,
            )
        if recur_every_s is not None:
            raise TriggerStoreError(
                "recur_every_s requires predicate fleet_idle",
                code=_REASON_RECUR_INVALID,
            )
        return None, None, None

    if predicate == PREDICATE_TRIGGER_TERMINAL:
        if expires_at is None:
            raise TriggerStoreError(
                "expires_at required when predicate is set",
                code=_REASON_EXPIRES_REQUIRED,
            )
        args = _parse_predicate_args(predicate_args)
        trigger_id = args.get("trigger_id")
        if not isinstance(trigger_id, str) or not trigger_id.strip():
            raise TriggerStoreError(
                "predicate_args.trigger_id must be a non-empty string",
                code=_REASON_MALFORMED_ARGS,
            )
        upstream = conn.execute(
            "SELECT id FROM triggers WHERE id = ?", (trigger_id,)
        ).fetchone()
        if upstream is None:
            raise TriggerStoreError(
                f"upstream trigger_id does not exist: {trigger_id}",
                code=_REASON_UNRESOLVABLE_UPSTREAM,
            )
        if recur_every_s is not None:
            raise TriggerStoreError(
                "recur_every_s is only valid with fleet_idle predicate",
                code=_REASON_RECUR_INVALID,
            )
        expires_iso = as_utc(expires_at).isoformat()
        return predicate, json.dumps(args), expires_iso

    if predicate == PREDICATE_FLEET_IDLE:
        args = _parse_predicate_args(predicate_args)
        _validate_fleet_idle_args(args)
        if recur_every_s is not None and (
            not isinstance(recur_every_s, int)
            or isinstance(recur_every_s, bool)
            or recur_every_s <= 0
        ):
            raise TriggerStoreError(
                "recur_every_s must be a positive integer",
                code=_REASON_RECUR_INVALID,
            )
        expires_iso = as_utc(expires_at).isoformat() if expires_at is not None else None
        return predicate, json.dumps(args), expires_iso

    raise TriggerStoreError(
        f"unknown predicate type: {predicate}",
        code=_REASON_UNKNOWN_PREDICATE,
    )


def eval_trigger_terminal(
    conn: sqlite3.Connection,
    predicate_args_json: str,
) -> bool:
    """True when upstream row has non-NULL terminal_status (optional filter)."""
    args = json.loads(predicate_args_json)
    trigger_id = args["trigger_id"]
    required_terminal = args.get("terminal_status")
    row = conn.execute(
        "SELECT terminal_status FROM triggers WHERE id = ?",
        (trigger_id,),
    ).fetchone()
    if row is None or row["terminal_status"] is None:
        return False
    if required_terminal is not None and row["terminal_status"] != required_terminal:
        return False
    return True


def eval_fleet_idle_predicate(
    predicate_args_json: str,
    *,
    snapshot: FleetIdleSnapshot,
    now_monotonic: float | None = None,
) -> bool:
    """Evaluate ``fleet_idle`` against a memoized pass snapshot."""
    args = json.loads(predicate_args_json)
    return eval_fleet_idle(snapshot, args, now_monotonic=now_monotonic)
