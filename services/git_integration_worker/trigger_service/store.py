"""Durable trigger schedule store at $DATA_DIR/trigger-schedule.sqlite."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from claude_bundles.operator_proxy_mission import OPERATOR_PROXY_MISSION_PURPOSES

from .db import apply_migrations, as_utc, connect, db_path, now_iso
from .models import (
    STATUS_CANCELLED,
    STATUS_FAILED,
    STATUS_FIRED,
    STATUS_FIRING,
    STATUS_SCHEDULED,
    TERMINAL_STATUSES,
    TriggerRow,
    TriggerStoreError,
    require_status,
    row_from_db,
    snapshot_prompt_text,
)
from .predicate_eval import validate_predicate_schedule
from .store_claim import claim_due as _claim_due
from .store_claim import expire_due as _expire_due
from .store_revoke import revoke_trigger as _revoke_trigger
from .story_envelope import elect_trigger_story_envelope


def _coalesce_skipped_periods(
    *,
    recur_s: int,
    fired_at_iso: str | None,
    terminal_at: datetime,
) -> int | None:
    """Recur periods elapsed between expected next slot and terminal reconcile."""
    if not fired_at_iso:
        return None
    fired_at = as_utc(datetime.fromisoformat(fired_at_iso))
    expected_next = fired_at + timedelta(seconds=recur_s)
    if terminal_at <= expected_next:
        return 0
    skipped = int((terminal_at - expected_next).total_seconds() // recur_s)
    return max(0, skipped)


class TriggerStore:
    """SQLite-backed trigger schedule CRUD + claim transitions."""

    def __init__(self, *, path: Path | None = None) -> None:
        self._path = path or db_path()
        with connect(self._path) as conn:
            apply_migrations(conn)

    def _connect(self):
        return connect(self._path)

    def schedule(
        self,
        *,
        created_by: str,
        fire_at: datetime,
        prompt_uri: str | None = None,
        prompt_text: str | None = None,
        purpose: str = "operator-proxy",
        model: str = "opus-5",
        arc: str | None = None,
        so_what: str | None = None,
        max_attempts: int = 3,
        predicate: str | None = None,
        predicate_args: str | dict | None = None,
        expires_at: datetime | None = None,
        recur_every_s: int | None = None,
        require_act_receipt: int | None = None,
        charter_root: str | None = None,
        window_index: int | None = None,
        _require_act_explicit: bool = False,
    ) -> TriggerRow:
        """Schedule a trigger row.

        When ``predicate`` is non-NULL, ``expires_at`` is required and
        ``predicate_args.trigger_id`` must resolve to an existing row.
        Refusal ``reason_code`` values: ``unknown_predicate_type``,
        ``expires_at_required``, ``unresolvable_upstream_trigger_id``,
        ``malformed_predicate_args``.
        """
        if not prompt_uri and not (prompt_text and prompt_text.strip()):
            raise TriggerStoreError("schedule requires prompt_uri or prompt_text")
        trigger_id = uuid.uuid4().hex
        created_at = now_iso()
        canonical_uri = prompt_uri
        if prompt_text and prompt_text.strip():
            canonical_uri = snapshot_prompt_text(
                trigger_id=trigger_id,
                prompt_text=prompt_text,
            )
        assert canonical_uri
        if not canonical_uri.startswith("cortex://"):
            raise TriggerStoreError("prompt_uri must use cortex:// scheme")
        stored_require = require_act_receipt
        if not _require_act_explicit and stored_require is None:
            if purpose in OPERATOR_PROXY_MISSION_PURPOSES:
                stored_require = 1
        envelope = elect_trigger_story_envelope(
            trigger_id=trigger_id,
            created_by=created_by,
            purpose=purpose,
            so_what=so_what,
            arc=arc,
            charter_root=charter_root,
            window_index=window_index,
        )
        with self._connect() as conn:
            pred, pred_args_json, expires_iso = validate_predicate_schedule(
                conn,
                predicate=predicate,
                predicate_args=predicate_args,
                expires_at=expires_at,
                recur_every_s=recur_every_s,
            )
            conn.execute(
                """
                INSERT INTO triggers (
                    id, created_at, created_by, fire_at, prompt_uri,
                    purpose, model, arc, so_what, status, max_attempts,
                    predicate, predicate_args, expires_at, require_act_receipt,
                    story_id, story_id_source, recur_every_s
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trigger_id,
                    created_at,
                    created_by,
                    as_utc(fire_at).isoformat(),
                    canonical_uri,
                    purpose,
                    model,
                    arc,
                    so_what,
                    require_status(STATUS_SCHEDULED),
                    max_attempts,
                    pred,
                    pred_args_json,
                    expires_iso,
                    stored_require,
                    envelope.story_id,
                    envelope.story_id_source,
                    recur_every_s,
                ),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM triggers WHERE id = ?", (trigger_id,)
            ).fetchone()
        assert row is not None
        return row_from_db(row)

    def rearm_recurring(
        self,
        trigger_id: str,
        *,
        terminal_at: datetime,
    ) -> TriggerRow | None:
        """After terminal reconcile, reschedule a ``recur_every_s`` row."""
        terminal_dt = as_utc(terminal_at)
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT recur_every_s, status, fired_at, fire_at
                FROM triggers WHERE id = ?
                """,
                (trigger_id,),
            ).fetchone()
            if row is None or row["recur_every_s"] is None:
                return None
            recur_s = int(row["recur_every_s"])
            next_fire = (terminal_dt + timedelta(seconds=recur_s)).isoformat()
            coalesce_skipped = _coalesce_skipped_periods(
                recur_s=recur_s,
                fired_at_iso=row["fired_at"],
                terminal_at=terminal_dt,
            )
            updated = conn.execute(
                """
                UPDATE triggers
                SET status = ?, fire_at = ?, execution_id = NULL, fired_at = NULL,
                    terminal_status = NULL, archive_uri = NULL, claimed_at = NULL,
                    attempts = 0, last_error = NULL, last_predicate_error = NULL,
                    last_coalesce_skipped = ?
                WHERE id = ? AND status = ? AND terminal_status IS NOT NULL
                """,
                (
                    require_status(STATUS_SCHEDULED),
                    next_fire,
                    coalesce_skipped,
                    trigger_id,
                    STATUS_FIRED,
                ),
            )
            if updated.rowcount != 1:
                return None
            conn.commit()
            full = conn.execute(
                "SELECT * FROM triggers WHERE id = ?", (trigger_id,)
            ).fetchone()
        return row_from_db(full) if full is not None else None

    def set_act_fields(
        self,
        trigger_id: str,
        *,
        act_status: str,
        act_evidence_uri: str | None = None,
        act_error: str | None = None,
    ) -> TriggerRow:
        """Update act verification columns without touching submit retry state."""
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE triggers
                SET act_status = ?, act_evidence_uri = ?, act_error = ?
                WHERE id = ?
                """,
                (
                    act_status,
                    act_evidence_uri,
                    act_error[:500] if act_error else None,
                    trigger_id,
                ),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM triggers WHERE id = ?", (trigger_id,)
            ).fetchone()
        assert row is not None
        return row_from_db(row)

    def list_triggers(self, *, limit: int = 100) -> list[TriggerRow]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM triggers ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [row_from_db(r) for r in rows]

    def get(self, trigger_id: str) -> TriggerRow | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM triggers WHERE id = ?", (trigger_id,)
            ).fetchone()
        return row_from_db(row) if row else None

    def revoke(self, trigger_id: str) -> TriggerRow:
        """Stop future fires for a recurring row (including in-flight episodes)."""
        with self._connect() as conn:
            return _revoke_trigger(conn, trigger_id)

    def cancel(self, trigger_id: str) -> TriggerRow:
        row = self.get(trigger_id)
        if row is None:
            raise TriggerStoreError(f"unknown trigger id: {trigger_id}")
        if row.status == STATUS_FIRED:
            raise TriggerStoreError(
                "cannot cancel a fired trigger; episode already launched",
                code="trigger_already_fired",
            )
        if row.status in TERMINAL_STATUSES:
            return row
        if row.status == STATUS_FIRING:
            raise TriggerStoreError(
                "cannot cancel trigger while firing",
                code="trigger_firing",
            )
        cancelled_at = now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE triggers
                SET status = ?, cancelled_at = ?
                WHERE id = ? AND status = ?
                """,
                (
                    require_status(STATUS_CANCELLED),
                    cancelled_at,
                    trigger_id,
                    STATUS_SCHEDULED,
                ),
            )
            conn.commit()
            updated = conn.execute(
                "SELECT * FROM triggers WHERE id = ?", (trigger_id,)
            ).fetchone()
        assert updated is not None
        return row_from_db(updated)

    def expire_due(
        self,
        *,
        now: datetime | None = None,
        _emit=None,
    ) -> list[TriggerRow]:
        """Mark scheduled rows past ``expires_at`` as ``expired`` (terminal).

        Only touches ``status='scheduled'`` rows. Expiry preempts remaining
        retries — a scheduled row with ``attempts > 0`` past ``expires_at``
        becomes ``expired``, not ``failed``. Emits ``giw.trigger.expired``
        post-commit for each actually-transitioned row.
        """
        return _expire_due(self._connect, now=now, _emit=_emit)

    def claim_due(
        self,
        *,
        now: datetime | None = None,
        _emit=None,
    ) -> TriggerRow | None:
        """Atomically claim one due scheduled row (skip-not-block).

        False/unknown predicates skip the candidate without blocking later
        candidates. Predicate evaluation never mutates ``attempts`` or
        ``status``. Candidate SELECT is capped at 50 rows ordered by
        ``fire_at`` — bounds per-scan eval cost so a large predicate
        backlog cannot stall a tick.
        """
        return _claim_due(self._connect, now=now, _emit=_emit)

    def mark_fired(self, trigger_id: str, *, execution_id: str) -> TriggerRow:
        fired_at = now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE triggers
                SET status = ?, execution_id = ?, fired_at = ?, last_error = NULL
                WHERE id = ? AND status = ?
                """,
                (
                    require_status(STATUS_FIRED),
                    execution_id,
                    fired_at,
                    trigger_id,
                    STATUS_FIRING,
                ),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM triggers WHERE id = ?", (trigger_id,)
            ).fetchone()
        assert row is not None
        return row_from_db(row)

    def mark_submit_retry(
        self,
        trigger_id: str,
        *,
        error: str,
        attempts: int,
        max_attempts: int,
    ) -> TriggerRow:
        """Revert firing row after retryable submit failure."""
        if attempts >= max_attempts:
            return self.mark_failed(trigger_id, error=error, attempts=attempts)
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE triggers
                SET status = ?, attempts = ?, last_error = ?, claimed_at = NULL
                WHERE id = ? AND status = ? AND execution_id IS NULL
                """,
                (
                    require_status(STATUS_SCHEDULED),
                    attempts,
                    error[:500],
                    trigger_id,
                    STATUS_FIRING,
                ),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM triggers WHERE id = ?", (trigger_id,)
            ).fetchone()
        assert row is not None
        return row_from_db(row)

    def mark_failed(
        self,
        trigger_id: str,
        *,
        error: str,
        attempts: int | None = None,
    ) -> TriggerRow:
        with self._connect() as conn:
            if attempts is not None:
                conn.execute(
                    """
                    UPDATE triggers
                    SET status = ?, last_error = ?, claimed_at = NULL, attempts = ?
                    WHERE id = ? AND status IN (?, ?)
                    """,
                    (
                        require_status(STATUS_FAILED),
                        error[:500],
                        attempts,
                        trigger_id,
                        STATUS_FIRING,
                        STATUS_SCHEDULED,
                    ),
                )
            else:
                conn.execute(
                    """
                    UPDATE triggers
                    SET status = ?, last_error = ?, claimed_at = NULL
                    WHERE id = ? AND status IN (?, ?)
                    """,
                    (
                        require_status(STATUS_FAILED),
                        error[:500],
                        trigger_id,
                        STATUS_FIRING,
                        STATUS_SCHEDULED,
                    ),
                )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM triggers WHERE id = ?", (trigger_id,)
            ).fetchone()
        assert row is not None
        return row_from_db(row)

    def reclaim_stale_firing(
        self,
        *,
        now: datetime | None = None,
        stale_after_s: float,
    ) -> list[TriggerRow]:
        """Bounded reclaim: firing rows with no execution_id past stale threshold."""
        now_dt = now or datetime.now(UTC)
        cutoff = (now_dt - timedelta(seconds=stale_after_s)).isoformat()
        reclaimed: list[TriggerRow] = []
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id FROM triggers
                WHERE status = ? AND execution_id IS NULL AND claimed_at < ?
                """,
                (STATUS_FIRING, cutoff),
            ).fetchall()
            for row in rows:
                trigger_id = row["id"]
                conn.execute(
                    """
                    UPDATE triggers
                    SET status = ?, claimed_at = NULL,
                        last_error = COALESCE(last_error, 'reclaimed stale firing')
                    WHERE id = ? AND status = ? AND execution_id IS NULL
                    """,
                    (
                        require_status(STATUS_SCHEDULED),
                        trigger_id,
                        STATUS_FIRING,
                    ),
                )
            conn.commit()
            for row in rows:
                full = conn.execute(
                    "SELECT * FROM triggers WHERE id = ?", (row["id"],)
                ).fetchone()
                if full is not None:
                    reclaimed.append(row_from_db(full))
        return reclaimed

    def list_pending_reconcile(self, *, limit: int = 20) -> list[TriggerRow]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM triggers
                WHERE status = ? AND execution_id IS NOT NULL
                  AND terminal_status IS NULL
                ORDER BY fired_at ASC
                LIMIT ?
                """,
                (STATUS_FIRED, limit),
            ).fetchall()
        return [row_from_db(r) for r in rows]

    def mark_reconciled(
        self,
        trigger_id: str,
        *,
        terminal_status: str,
        archive_uri: str | None = None,
        error: str | None = None,
    ) -> TriggerRow:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE triggers
                SET terminal_status = ?, archive_uri = ?,
                    last_error = COALESCE(?, last_error)
                WHERE id = ? AND status = ? AND terminal_status IS NULL
                """,
                (
                    terminal_status,
                    archive_uri,
                    error[:500] if error else None,
                    trigger_id,
                    STATUS_FIRED,
                ),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM triggers WHERE id = ?", (trigger_id,)
            ).fetchone()
        assert row is not None
        return row_from_db(row)
