"""Event-driven composer deferral for display endpoint wakes (no wall-clock retry)."""

from __future__ import annotations

import logging
import re
from typing import Any

from agent_bus_store.db.connection import write_connect
from agent_bus_store.db.endpoint_wake_ledger import _now

logger = logging.getLogger(__name__)

COMPOSER_DEFER_REASONS = frozenset(
    {
        "composer_not_empty",
        "composer_busy",
        "active_chat_busy",
        "non_empty_composer",
    }
)
COMPOSER_DEFER_ARMED = "composer_defer:armed"
COMPOSER_DEFER_SWEEP = "composer_defer:sweep_eligible"
COMPOSER_DEFER_ESCALATED = "composer_defer:escalated"
COMPOSER_DEFER_ESCALATED_NOTICED = "composer_defer:escalated_noticed"
LEGACY_NORMALIZE_AGENT_MISMATCH = "legacy_normalize:agent_id_mismatch"
_ESCALATION_SUBJECT = "NOTICE|endpoint wake composer defer exhausted"
_DEFER_PREFIX = "composer_defer:"


def composer_defer_reasons_sorted() -> tuple[str, ...]:
    """Stable binding order for SQL ``IN`` placeholders."""
    return tuple(sorted(COMPOSER_DEFER_REASONS))


def composer_defer_reason_in_sql() -> str:
    """``(?,?,...)`` placeholder count matches ``COMPOSER_DEFER_REASONS``."""
    n = len(COMPOSER_DEFER_REASONS)
    return f"({','.join('?' * n)})"


def is_composer_defer_reason(reason: str | None) -> bool:
    return str(reason or "").strip() in COMPOSER_DEFER_REASONS


def composer_defer_blocks_claim(
    *,
    reason: str | None,
    decided_by_rule: str | None,
    agent: str | None = None,
) -> bool:
    """Pending composer-defers are not re-claimed until empty-event redrive or sweep refuse."""
    if not is_composer_defer_reason(reason):
        return False
    if str(agent or "").strip().lower() == "orion":
        # Orion/chatgpt_work uses bounded wall-clock deferred_streaming + owner retries;
        # do not feed the generic display sweep that terminalizes on one pass.
        return False
    rule = str(decided_by_rule or "")
    return rule.startswith(_DEFER_PREFIX)


def merge_composer_defer_decided_by_rule(
    decided_by_rule: str | None, *, reason: str | None
) -> str | None:
    if not is_composer_defer_reason(reason):
        return decided_by_rule
    rule = str(decided_by_rule or "")
    if rule.startswith(_DEFER_PREFIX):
        return decided_by_rule
    return COMPOSER_DEFER_ARMED


def normalize_legacy_pending_wakes() -> dict[str, int]:
    """One idempotent pass: terminal agent_id_mismatch; arm legacy composer defers."""
    reasons = composer_defer_reasons_sorted()
    in_sql = composer_defer_reason_in_sql()
    with write_connect() as conn:
        mismatch = conn.execute(
            "UPDATE endpoint_wake_ledger SET state='refused', "
            "decided_by_rule=?, finished_at=datetime('now') "
            "WHERE state='pending' AND reason='agent_id_mismatch'",
            (LEGACY_NORMALIZE_AGENT_MISMATCH,),
        )
        composer = conn.execute(
            "UPDATE endpoint_wake_ledger SET decided_by_rule=? "
            f"WHERE state='pending' AND reason IN {in_sql} "
            "AND lower(trim(agent)) <> 'orion' "
            "AND (decided_by_rule IS NULL OR decided_by_rule NOT LIKE ?)",
            (
                COMPOSER_DEFER_ARMED,
                *reasons,
                f"{_DEFER_PREFIX}%",
            ),
        )
    return {
        "agent_id_mismatch_refused": int(mismatch.rowcount),
        "composer_defer_armed": int(composer.rowcount),
    }


def run_dispatcher_pass_legacy_sweep(store: Any) -> dict[str, int]:
    """Test/dispatch hook: normalize, escalate, finalize (one outer-pass legacy slice)."""
    out = dict(normalize_legacy_pending_wakes())
    out["escalated"] = escalate_composer_deferred_on_sweep(store)
    out["finalized"] = finalize_composer_defer_pass()
    return out


def finalize_composer_defer_pass() -> int:
    """End of one dispatcher outer pass: armed → sweep_eligible."""
    reasons = composer_defer_reasons_sorted()
    in_sql = composer_defer_reason_in_sql()
    with write_connect() as conn:
        cur = conn.execute(
            "UPDATE endpoint_wake_ledger SET decided_by_rule=? "
            f"WHERE state='pending' AND reason IN {in_sql} AND decided_by_rule=? "
            "AND lower(trim(agent)) <> 'orion'",
            (
                COMPOSER_DEFER_SWEEP,
                *reasons,
                COMPOSER_DEFER_ARMED,
            ),
        )
        return int(cur.rowcount)


def _maestro_escalation_body(
    representative: dict[str, Any], group: list[dict[str, Any]]
) -> str:
    parts = ", ".join(
        f"agent={item.get('agent')} id={item.get('id')}" for item in group
    )
    return (
        f"thread={representative.get('thread_id')} "
        f"turn_number={representative.get('turn_number')} "
        f"count={len(group)} rows={parts} reason={representative.get('reason')}"
    )


def escalate_composer_deferred_on_sweep(store: Any, *, stamp: str | None = None) -> int:
    """Backoff retry or terminal drop for sweep-eligible non-orion composer defers.

    Returns the number of rows dropped at cap in this pass.
    """
    from agent_bus_store.db.endpoint_wake_attempts import (
        COMPOSER_DEFER_DROP_NOTICED,
        DISPLAY_COMPOSER_DEFER_MAX_ATTEMPTS,
        composer_defer_over_cap,
        schedule_composer_defer_retry,
        terminal_drop_composer_defer_row,
    )

    stamp = stamp or _now(None)
    with write_connect() as conn:
        cols = {
            str(r[1]) for r in conn.execute("PRAGMA table_info(endpoint_wake_ledger)")
        }
        select_extra = ""
        if "wake_attempt_count" in cols:
            select_extra += ", wake_attempt_count"
        if "enqueued_at" in cols:
            select_extra += ", enqueued_at"
        if "created_at" in cols:
            select_extra += ", created_at"
        if "subscription_id" in cols:
            select_extra += ", subscription_id"
        if "wake_next_attempt_at" in cols:
            select_extra += ", wake_next_attempt_at"
        where_extra = ""
        query_params: list[Any] = [COMPOSER_DEFER_SWEEP]
        if "wake_next_attempt_at" in cols and "wake_attempt_count" in cols:
            where_extra = (
                " AND (COALESCE(wake_attempt_count, 0) >= ? "
                "OR wake_next_attempt_at IS NULL "
                "OR datetime(wake_next_attempt_at) <= datetime(?))"
            )
            query_params.extend([DISPLAY_COMPOSER_DEFER_MAX_ATTEMPTS, stamp])
        elif "wake_next_attempt_at" in cols:
            where_extra = (
                " AND (wake_next_attempt_at IS NULL "
                "OR datetime(wake_next_attempt_at) <= datetime(?))"
            )
            query_params.append(stamp)
        rows = conn.execute(
            "SELECT id, agent, thread_id, turn_number, reason, decided_by_rule"
            f"{select_extra} FROM endpoint_wake_ledger "
            "WHERE state='pending' AND decided_by_rule=? "
            "AND lower(trim(agent)) <> 'orion' "
            f"{where_extra} ORDER BY id",
            tuple(query_params),
        ).fetchall()
        if not rows:
            return 0
        items = [dict(row) for row in rows]
        drop_rows: list[dict[str, Any]] = []
        retry_rows: list[dict[str, Any]] = []
        by_sub: dict[str, list[dict[str, Any]]] = {}
        for item in items:
            sub_key = str(item.get("subscription_id") or item["id"])
            by_sub.setdefault(sub_key, []).append(item)
        for group in by_sub.values():
            live = min(group, key=lambda r: int(r["id"]))
            if composer_defer_over_cap(live, stamp=stamp):
                drop_rows.extend(group)
            else:
                retry_rows.append(live)
        pending_notices: list[dict[str, Any]] = []
        for item in drop_rows:
            if str(item.get("decided_by_rule") or "") == COMPOSER_DEFER_DROP_NOTICED:
                continue
            terminal_drop_composer_defer_row(
                conn, item, stamp=stamp, pending_notices=pending_notices
            )
        schedule_composer_defer_retry(conn, retry_rows, stamp=stamp)
        dropped = len(drop_rows)
    stamp_fn = getattr(store, "create_turn", None)
    if stamp_fn is not None:
        for notice in pending_notices:
            try:
                stamp_fn(**notice)
            except Exception:
                logger.exception(
                    "composer_defer drop notice failed row=%s",
                    notice.get("wake_row_id"),
                )
    return dropped


def notify_composer_empty_for_target(
    *,
    display: str,
    surface: str,
    agent_id: str,
    thread_scope: str,
) -> int:
    """Composer-empty observation: clear defer tags so owed wakes can reclaim."""
    _ = display
    if str(surface or "").strip().lower() not in {"cursor", "grok"}:
        return 0
    aid = str(agent_id or "").strip()
    scope = str(thread_scope or "").strip()
    if not aid or not scope:
        return 0
    reasons = composer_defer_reasons_sorted()
    in_sql = composer_defer_reason_in_sql()
    with write_connect() as conn:
        pending = conn.execute(
            "UPDATE endpoint_wake_ledger SET decided_by_rule=NULL "
            f"WHERE state='pending' AND thread_id=? AND reason IN {in_sql} "
            "AND decided_by_rule LIKE ?",
            (scope, *reasons, f"{_DEFER_PREFIX}%"),
        )
        rearmed = conn.execute(
            "UPDATE endpoint_wake_ledger SET state='pending', decided_by_rule=NULL, "
            f"finished_at=NULL WHERE state='refused' AND decided_by_rule IN (?,?) "
            f"AND thread_id=? AND reason IN {in_sql}",
            (
                COMPOSER_DEFER_ESCALATED,
                COMPOSER_DEFER_ESCALATED_NOTICED,
                scope,
                *reasons,
            ),
        )
        return int(pending.rowcount) + int(rearmed.rowcount)


def defer_path_avoids_wall_clock() -> bool:
    """Test hook: composer defer module must not use wall-clock for defer decisions."""
    import inspect

    source = inspect.getsource(escalate_composer_deferred_on_sweep)
    source += inspect.getsource(notify_composer_empty_for_target)
    source += inspect.getsource(composer_defer_blocks_claim)
    return not re.search(r"\btime\.(?:time|monotonic|sleep)\b", source) and (
        "datetime(" not in source
        or "datetime('now')" in source
        or "datetime(wake_next_attempt_at)" in source
    )


__all__ = [
    "COMPOSER_DEFER_ARMED",
    "COMPOSER_DEFER_ESCALATED",
    "COMPOSER_DEFER_ESCALATED_NOTICED",
    "COMPOSER_DEFER_REASONS",
    "COMPOSER_DEFER_SWEEP",
    "LEGACY_NORMALIZE_AGENT_MISMATCH",
    "composer_defer_reason_in_sql",
    "composer_defer_reasons_sorted",
    "composer_defer_blocks_claim",
    "defer_path_avoids_wall_clock",
    "escalate_composer_deferred_on_sweep",
    "finalize_composer_defer_pass",
    "is_composer_defer_reason",
    "merge_composer_defer_decided_by_rule",
    "normalize_legacy_pending_wakes",
    "notify_composer_empty_for_target",
    "run_dispatcher_pass_legacy_sweep",
]
