"""Closed-catalog predicate validation and evaluation (v0: trigger_terminal)."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any

from .db import as_utc
from .models import (
    PREDICATE_TRIGGER_TERMINAL,
    TriggerStoreError,
)

_REASON_UNKNOWN_PREDICATE = "unknown_predicate_type"
_REASON_EXPIRES_REQUIRED = "expires_at_required"
_REASON_UNRESOLVABLE_UPSTREAM = "unresolvable_upstream_trigger_id"
_REASON_MALFORMED_ARGS = "malformed_predicate_args"


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


def validate_predicate_schedule(
    conn: sqlite3.Connection,
    *,
    predicate: str | None,
    predicate_args: str | dict[str, Any] | None,
    expires_at: datetime | None,
) -> tuple[str | None, str | None, str | None]:
    """Return (predicate, predicate_args_json, expires_at_iso) or raise."""
    if predicate is None:
        if predicate_args is not None or expires_at is not None:
            raise TriggerStoreError(
                "predicate_args and expires_at require predicate",
                code=_REASON_MALFORMED_ARGS,
            )
        return None, None, None
    if predicate != PREDICATE_TRIGGER_TERMINAL:
        raise TriggerStoreError(
            f"unknown predicate type: {predicate}",
            code=_REASON_UNKNOWN_PREDICATE,
        )
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
    expires_iso = as_utc(expires_at).isoformat()
    return predicate, json.dumps(args), expires_iso


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
