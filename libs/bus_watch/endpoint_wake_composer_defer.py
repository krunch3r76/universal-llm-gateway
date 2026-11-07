"""Event-driven composer deferral for display endpoint wakes (no wall-clock retry)."""

from __future__ import annotations

import logging
import re
from typing import Any

from agent_bus_store.db.connection import write_connect

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
    *, reason: str | None, decided_by_rule: str | None
) -> bool:
    """Pending composer-defers are not re-claimed until empty-event redrive or sweep refuse."""
    if not is_composer_defer_reason(reason):
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
            f"WHERE state='pending' AND reason IN {in_sql} AND decided_by_rule=?",
            (
                COMPOSER_DEFER_SWEEP,
                *reasons,
                COMPOSER_DEFER_ARMED,
            ),
        )
        return int(cur.rowcount)


def _maestro_escalation_body(row: dict[str, Any]) -> str:
    return (
        f"thread={row.get('thread_id')} agent={row.get('agent')} "
        f"turn_number={row.get('turn_number')} reason={row.get('reason')}"
    )


def escalate_composer_deferred_on_sweep(store: Any) -> int:
    """Refuse sweep-eligible composer defers and post one maestro turn per row."""
    escalated_items: list[dict[str, Any]] = []
    with write_connect() as conn:
        rows = conn.execute(
            "SELECT id, agent, thread_id, turn_number, reason, decided_by_rule "
            "FROM endpoint_wake_ledger "
            "WHERE state='pending' AND decided_by_rule=?",
            (COMPOSER_DEFER_SWEEP,),
        ).fetchall()
        if not rows:
            return 0
        for row in rows:
            item = dict(row)
            rid = int(item["id"])
            conn.execute(
                "UPDATE endpoint_wake_ledger SET state='refused', "
                "decided_by_rule=?, finished_at=datetime('now') WHERE id=?",
                (COMPOSER_DEFER_ESCALATED, rid),
            )
            escalated_items.append(item)
    stamp_fn = getattr(store, "create_turn", None)
    count = 0
    for item in escalated_items:
        if stamp_fn is None:
            count += 1
            continue
        try:
            stamp_fn(
                thread=str(item["thread_id"]),
                from_agent="agent_bus",
                to_agent="maestro",
                subject=_ESCALATION_SUBJECT,
                body=_maestro_escalation_body(item),
            )
        except Exception:
            logger.exception(
                "composer_defer escalation failed thread=%s",
                item.get("thread_id"),
            )
        count += 1
    return count


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
        cur = conn.execute(
            "UPDATE endpoint_wake_ledger SET decided_by_rule=NULL "
            f"WHERE state='pending' AND thread_id=? AND reason IN {in_sql} "
            "AND decided_by_rule LIKE ?",
            (scope, *reasons, f"{_DEFER_PREFIX}%"),
        )
        return int(cur.rowcount)


def defer_path_avoids_wall_clock() -> bool:
    """Test hook: composer defer module must not use wall-clock for defer decisions."""
    import inspect

    source = inspect.getsource(escalate_composer_deferred_on_sweep)
    source += inspect.getsource(notify_composer_empty_for_target)
    source += inspect.getsource(composer_defer_blocks_claim)
    return not re.search(r"\btime\.(?:time|monotonic|sleep)\b", source) and (
        "datetime(" not in source or "datetime('now')" in source
    )


__all__ = [
    "COMPOSER_DEFER_ARMED",
    "COMPOSER_DEFER_ESCALATED",
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
