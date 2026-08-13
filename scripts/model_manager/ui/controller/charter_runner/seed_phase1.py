"""Bootstrap legacy Phase-1 roots via typed admit (SeedConfirm migration only)."""

from __future__ import annotations

from dataclasses import replace

from scripts.model_manager.ui.controller.charter_runner import window_log
from scripts.model_manager.ui.controller.charter_runner.admission.typed_work_item import (
    typed_record_valid,
)
from scripts.model_manager.ui.controller.charter_runner.root_ledger import (
    RootLedgerRow,
    SeedConfirm,
    TypedWorkItemAdmit,
    admit_work_item,
    load_all_roots,
    load_root,
    open_default_ledger,
    upsert_root,
)
from scripts.model_manager.ui.controller.charter_runner.window_sequence import (
    window_id_for,
)

PHASE1_SEEDS: tuple[SeedConfirm, ...] = (
    SeedConfirm(
        root_id="5975",
        pickup_gid="G7",
        pickup_lane="judgment",
        attendance="autonomous",
        scoreboard_uri="cortex://notes/system/threads/5975-charter-scoreboard.md",
    ),
    SeedConfirm(
        root_id="5993",
        pickup_gid="G1",
        pickup_lane="judgment",
        attendance="attended",
        scoreboard_uri="cortex://notes/system/threads/5993-charter-scoreboard.md",
    ),
    SeedConfirm(
        root_id="5994",
        pickup_gid="G1",
        pickup_lane="judgment",
        attendance="attended",
        scoreboard_uri="cortex://notes/system/threads/5994-charter-scoreboard.md",
    ),
    # Phase 3 sole-admitter: continuous drive root (G3 implement).
    SeedConfirm(
        root_id="6091",
        pickup_gid="G3",
        pickup_lane="mechanical",
        pickup_executor="cursor/composer-2.5",
        attendance="autonomous",
        scoreboard_uri=(
            "cortex://notes/system/threads/"
            "charter-tick-kernel-continuous-scoreboard.md"
        ),
    ),
    # Friction conveyor — must be ledger-seeded before Phase-3 manage recycle
    # (a:26610 / a:26619). attendance=attended ⇒ ADMIT_WORKER (not consult).
    # 6110 closed 2026-07-28 (executor-mismatch storm); rewrite root=6171.
    # Ledger defaults (attended·G9·judgment·grok) do NOT license admit-shaped
    # empty standing tip birth — tip birth-shape owns legality (marked wait with
    # explicit executor=pending fenced by empty_hopper NOOP, or concrete work
    # + concrete executor=cursor/*); see orchestrator-workflow R12 (a:26710).
    SeedConfirm(
        root_id="6171",
        pickup_gid="G9",
        pickup_lane="judgment",
        pickup_executor="cursor/grok-4.6",
        attendance="attended",
        scoreboard_uri="cortex://notes/system/threads/6171-charter-scoreboard.md",
    ),
)

_PHASE1_BY_ID = {seed.root_id: seed for seed in PHASE1_SEEDS}


def _seed_to_typed(confirm: SeedConfirm) -> TypedWorkItemAdmit:
    """Map legacy SeedConfirm into typed admit (one-time migration surface)."""
    return TypedWorkItemAdmit(
        root_id=confirm.root_id,
        pickup_gid=confirm.pickup_gid,
        pickup_lane=confirm.pickup_lane,
        attendance=confirm.attendance,
        pickup_executor=confirm.pickup_executor,
        scoreboard_uri=confirm.scoreboard_uri,
    )


def _reconcile_window_history(conn, row: RootLedgerRow) -> RootLedgerRow:
    """Carry a root's existing window history onto a fresh seed row.

    A seed writes ``last_window_id=None``, which reads as "no window ever ran" — so
    a root re-seeded after a manage recycle restarted numbering at w1 and re-fired
    packets that already existed (a:26592). The transcript index is the sync-readable
    record of what already ran; bus pointers remain authoritative at admit time,
    where ``next_window_index`` takes the max of all three.
    """
    high = window_log.max_window_index(row.root_id)
    if high <= 0:
        return row
    reconciled = replace(row, last_window_id=window_id_for(row.root_id, high))
    upsert_root(conn, reconciled)
    return reconciled


def ensure_root_ledger_seed(
    root_id: str,
    *,
    default: SeedConfirm | None = None,
) -> bool:
    """Legacy migration hook — new standing work must use ``admit_work_item``.

    Returns True when a typed row exists after the call. Fails closed when no
    migration source is known (no silent skip-forever).
    """
    if not root_id:
        return False
    confirm = _PHASE1_BY_ID.get(root_id) or default
    conn = open_default_ledger()
    try:
        existing = load_root(conn, root_id)
        if existing is not None:
            return typed_record_valid(existing)
        if confirm is None:
            return False
        if confirm.root_id != root_id:
            confirm = SeedConfirm(
                root_id=root_id,
                pickup_gid=confirm.pickup_gid,
                pickup_lane=confirm.pickup_lane,
                attendance=confirm.attendance,
                pickup_executor=confirm.pickup_executor,
                scoreboard_uri=confirm.scoreboard_uri,
            )
        row = admit_work_item(conn, _seed_to_typed(confirm))
        _reconcile_window_history(conn, row)
        return typed_record_valid(row)
    finally:
        conn.close()


def seed_phase1_roots() -> list[dict]:
    """Migrate human-confirmed Phase-1 seeds via typed admit; return summaries."""
    conn = open_default_ledger()
    try:
        results = []
        for confirm in PHASE1_SEEDS:
            row = _reconcile_window_history(
                conn, admit_work_item(conn, _seed_to_typed(confirm))
            )
            results.append(
                {
                    "root": row.root_id,
                    "status": row.status.value,
                    "pickup": row.pickup_gid,
                    "lane": row.pickup_lane,
                    "attendance": row.attendance,
                    "last_window_id": row.last_window_id,
                }
            )
        return results
    finally:
        conn.close()


def dump_seeded_rows() -> list[dict]:
    """Summarize every row currently stored in the default ledger (CLI helper)."""
    conn = open_default_ledger()
    try:
        return [
            {
                "root": r.root_id,
                "status": r.status.value,
                "pickup": r.pickup_gid,
                "lane": r.pickup_lane,
                "attendance": r.attendance,
            }
            for r in load_all_roots(conn)
        ]
    finally:
        conn.close()


if __name__ == "__main__":
    import json

    print(json.dumps(seed_phase1_roots(), indent=2))
