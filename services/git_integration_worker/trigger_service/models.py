"""Trigger row model, errors, and prompt snapshot helpers."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from durable_io.atomic import durable_write_text
from implement_admission.closeout_helpers import cortex_files_root

PROMPT_PREFIX = "notes/system/ephemeral/trigger-schedule"
PREDICATE_TRIGGER_TERMINAL = "trigger_terminal"
PREDICATE_FLEET_IDLE = "fleet_idle"
PREDICATE_DEFERRING = frozenset({PREDICATE_FLEET_IDLE})
STATUS_SCHEDULED = "scheduled"
STATUS_FIRING = "firing"
STATUS_FIRED = "fired"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"
STATUS_EXPIRED = "expired"
ALL_STATUSES = frozenset(
    {
        STATUS_SCHEDULED,
        STATUS_FIRING,
        STATUS_FIRED,
        STATUS_FAILED,
        STATUS_CANCELLED,
        STATUS_EXPIRED,
    }
)
TERMINAL_STATUSES = {STATUS_FAILED, STATUS_CANCELLED, STATUS_EXPIRED}


class TriggerStoreError(ValueError):
    """Schedule or lifecycle refusal."""

    def __init__(self, message: str, *, code: str = "trigger_refused") -> None:
        super().__init__(message)
        self.code = code


def require_status(status: str) -> str:
    """Application-enforced status domain (A3 compensating control for dropped CHECK)."""
    if status not in ALL_STATUSES:
        raise TriggerStoreError(
            f"invalid trigger status: {status}",
            code="invalid_trigger_status",
        )
    return status


@dataclass(frozen=True, slots=True)
class TriggerRow:
    id: str
    created_at: str
    created_by: str
    fire_at: str
    prompt_uri: str
    purpose: str
    model: str
    arc: str | None
    so_what: str | None
    status: str
    attempts: int
    max_attempts: int
    last_error: str | None
    claimed_at: str | None
    execution_id: str | None
    fired_at: str | None
    terminal_status: str | None
    archive_uri: str | None
    cancelled_at: str | None
    predicate: str | None
    predicate_args: str | None
    expires_at: str | None
    last_predicate_error: str | None
    act_status: str = "n/a"
    act_evidence_uri: str | None = None
    act_error: str | None = None
    require_act_receipt: int | None = None
    story_id: str | None = None
    story_id_source: str | None = None
    recur_every_s: int | None = None
    defer_count: int = 0
    last_deferred_at: str | None = None
    last_fleet_verdict: str | None = None
    degraded: int = 0
    last_coalesce_skipped: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "created_at": self.created_at,
            "created_by": self.created_by,
            "fire_at": self.fire_at,
            "prompt_uri": self.prompt_uri,
            "purpose": self.purpose,
            "model": self.model,
            "arc": self.arc,
            "so_what": self.so_what,
            "status": self.status,
            "attempts": self.attempts,
            "max_attempts": self.max_attempts,
            "last_error": self.last_error,
            "claimed_at": self.claimed_at,
            "execution_id": self.execution_id,
            "fired_at": self.fired_at,
            "terminal_status": self.terminal_status,
            "archive_uri": self.archive_uri,
            "cancelled_at": self.cancelled_at,
            "predicate": self.predicate,
            "predicate_args": (
                json.loads(self.predicate_args) if self.predicate_args else None
            ),
            "expires_at": self.expires_at,
            "last_predicate_error": self.last_predicate_error,
            "act_status": self.act_status,
            "act_evidence_uri": self.act_evidence_uri,
            "act_error": self.act_error,
            "require_act_receipt": self.require_act_receipt,
            "story_id": self.story_id,
            "story_id_source": self.story_id_source,
            "recur_every_s": self.recur_every_s,
            "defer_count": self.defer_count,
            "last_deferred_at": self.last_deferred_at,
            "last_fleet_verdict": self.last_fleet_verdict,
            "degraded": bool(self.degraded),
            "last_coalesce_skipped": self.last_coalesce_skipped,
        }


def row_from_db(row: sqlite3.Row) -> TriggerRow:
    return TriggerRow(
        id=row["id"],
        created_at=row["created_at"],
        created_by=row["created_by"],
        fire_at=row["fire_at"],
        prompt_uri=row["prompt_uri"],
        purpose=row["purpose"],
        model=row["model"],
        arc=row["arc"],
        so_what=row["so_what"],
        status=row["status"],
        attempts=row["attempts"],
        max_attempts=row["max_attempts"],
        last_error=row["last_error"],
        claimed_at=row["claimed_at"],
        execution_id=row["execution_id"],
        fired_at=row["fired_at"],
        terminal_status=row["terminal_status"],
        archive_uri=row["archive_uri"],
        cancelled_at=row["cancelled_at"],
        predicate=row["predicate"],
        predicate_args=row["predicate_args"],
        expires_at=row["expires_at"],
        last_predicate_error=row["last_predicate_error"],
        act_status=row["act_status"] if "act_status" in row.keys() else "n/a",
        act_evidence_uri=row["act_evidence_uri"]
        if "act_evidence_uri" in row.keys()
        else None,
        act_error=row["act_error"] if "act_error" in row.keys() else None,
        require_act_receipt=row["require_act_receipt"]
        if "require_act_receipt" in row.keys()
        else None,
        story_id=row["story_id"] if "story_id" in row.keys() else None,
        story_id_source=row["story_id_source"]
        if "story_id_source" in row.keys()
        else None,
        recur_every_s=int(row["recur_every_s"])
        if "recur_every_s" in row.keys() and row["recur_every_s"] is not None
        else None,
        defer_count=int(row["defer_count"]) if "defer_count" in row.keys() else 0,
        last_deferred_at=row["last_deferred_at"]
        if "last_deferred_at" in row.keys()
        else None,
        last_fleet_verdict=row["last_fleet_verdict"]
        if "last_fleet_verdict" in row.keys()
        else None,
        degraded=int(row["degraded"]) if "degraded" in row.keys() else 0,
        last_coalesce_skipped=int(row["last_coalesce_skipped"])
        if "last_coalesce_skipped" in row.keys()
        and row["last_coalesce_skipped"] is not None
        else None,
    )


def snapshot_prompt_text(*, trigger_id: str, prompt_text: str) -> str:
    """Persist inline prompt to cortex share; return canonical cortex:// URI."""
    rel = f"{PROMPT_PREFIX}/{trigger_id}/prompt.md"
    dest = cortex_files_root() / rel
    durable_write_text(dest, prompt_text, retain_store_root=cortex_files_root())
    return f"cortex://{rel}"
