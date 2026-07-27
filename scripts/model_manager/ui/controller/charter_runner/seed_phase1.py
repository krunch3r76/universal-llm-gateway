"""Seed enrolled roots for Phase 1 shadow kernel (human-confirmed §F.1)."""

from __future__ import annotations

from scripts.model_manager.ui.controller.charter_runner.root_ledger import (
    SeedConfirm,
    load_all_roots,
    open_default_ledger,
    seed_from_confirm,
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
)


def seed_phase1_roots() -> list[dict]:
    """Apply human-confirmed seeds; return row summaries."""
    conn = open_default_ledger()
    try:
        results = []
        for confirm in PHASE1_SEEDS:
            row = seed_from_confirm(conn, confirm)
            results.append(
                {
                    "root": row.root_id,
                    "status": row.status.value,
                    "pickup": row.pickup_gid,
                    "lane": row.pickup_lane,
                    "attendance": row.attendance,
                }
            )
        return results
    finally:
        conn.close()


def dump_seeded_rows() -> list[dict]:
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
