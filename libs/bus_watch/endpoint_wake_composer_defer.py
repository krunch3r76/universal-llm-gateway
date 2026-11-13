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


def escalate_composer_deferred_on_sweep(store: Any) -> int:
    """Refuse sweep-eligible composer defers; post at most one maestro NOTICE per (thread, turn).

    Returns the number of ledger rows refused in this pass (not the number of bus posts).
    """
    with write_connect() as conn:
        rows = conn.execute(
            "SELECT id, agent, thread_id, turn_number, reason, decided_by_rule "
            "FROM endpoint_wake_ledger "
            "WHERE state='pending' AND decided_by_rule=? "
            "AND lower(trim(agent)) <> 'orion' "
            "ORDER BY id",
            (COMPOSER_DEFER_SWEEP,),
        ).fetchall()
        if not rows:
            return 0
        items = [dict(row) for row in rows]

        groups: dict[tuple[str, int], list[dict[str, Any]]] = {}
        for item in items:
            key = (str(item["thread_id"]), int(item["turn_number"]))
            groups.setdefault(key, []).append(item)

        thread_ids = tuple({str(item["thread_id"]) for item in items})
        in_threads = ",".join("?" * len(thread_ids))
        noticed_rows = conn.execute(
            "SELECT thread_id, turn_number FROM endpoint_wake_ledger "
            f"WHERE state='refused' AND decided_by_rule=? "
            f"AND thread_id IN ({in_threads})",
            (COMPOSER_DEFER_ESCALATED_NOTICED, *thread_ids),
        ).fetchall()
        noticed_keys = {
            (str(row["thread_id"]), int(row["turn_number"])) for row in noticed_rows
        }

        groups_to_notice: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
        for key, group in groups.items():
            if key not in noticed_keys:
                groups_to_notice.append((group[0], group))

        for item in items:
            rid = int(item["id"])
            conn.execute(
                "UPDATE endpoint_wake_ledger SET state='refused', "
                "decided_by_rule=?, finished_at=datetime('now') WHERE id=?",
                (COMPOSER_DEFER_ESCALATED, rid),
            )
        refused_count = len(items)

    stamp_fn = getattr(store, "create_turn", None)
    for representative, group in groups_to_notice:
        if stamp_fn is None:
            continue
        thread_id = representative.get("thread_id")
        turn_number = representative.get("turn_number")
        row_ids = [int(item["id"]) for item in group]
        agents = [str(item.get("agent")) for item in group]
        try:
            stamp_fn(
                thread=str(thread_id),
                from_agent="agent_bus",
                to_agent="maestro",
                subject=_ESCALATION_SUBJECT,
                body=_maestro_escalation_body(representative, group),
            )
            rep_id = int(representative["id"])
            with write_connect() as conn:
                conn.execute(
                    "UPDATE endpoint_wake_ledger SET decided_by_rule=? "
                    "WHERE id=? AND state='refused'",
                    (COMPOSER_DEFER_ESCALATED_NOTICED, rep_id),
                )
        except Exception:
            logger.exception(
                "composer_defer escalation failed thread=%s turn_number=%s "
                "agents=%s row_ids=%s",
                thread_id,
                turn_number,
                agents,
                row_ids,
            )
    return refused_count


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
        "datetime(" not in source or "datetime('now')" in source
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
