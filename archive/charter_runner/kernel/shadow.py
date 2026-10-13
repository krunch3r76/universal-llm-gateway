"""Shadow-mode charter kernel — decide only; old tick remains sole admitter."""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from libs.charter_runner_store.db import charter_runner_data_dir

from ..admission import (
    CapStore,
    CapsView,
    EnvFacts,
    classify_shadow_diff,
    decide,
    map_old_skip_to_kernel,
)
from ..env_snapshot import EnvSnapshot
from ..root_ledger import Transition, load_all_roots, open_default_ledger

SHADOW_LEDGER_STARVE_ROOT = "_shadow_ledger_"
SHADOW_STARVE_CLASS = "starved:ledger_empty"


@dataclass(frozen=True)
class ShadowPassResult:
    rows: list[dict[str, Any]]
    starved: bool = False
    starve_reason: str | None = None
    bus_roots: int = 0


@dataclass(frozen=True)
class ShadowDiffRow:
    ts: float
    root: str
    old_decision: str
    kernel_transition: str
    classification: str


class ShadowKernel:
    """Phase 1 instrument — records shadow decisions beside the live tick."""

    def __init__(
        self,
        *,
        caps: CapStore | None = None,
        enrolled_roots: list[str] | None = None,
    ) -> None:
        self._caps = caps or CapStore()
        self._enrolled = enrolled_roots or []

    def shadow_pass(
        self,
        *,
        old_decisions: dict[str, str],
        env: EnvSnapshot,
    ) -> list[ShadowDiffRow]:
        """Run decide for each ledger root; compare to old tick outcomes."""
        conn = open_default_ledger()
        try:
            rows = load_all_roots(conn)
        finally:
            conn.close()
        if not rows:
            return []
        snapshot = env
        diffs: list[ShadowDiffRow] = []
        for state in rows:
            old = old_decisions.get(state.root_id, "noop")
            caps_view = CapsView.from_cap_store(self._caps, state.root_id)
            has_wip = old == "window_in_flight"
            facts = snapshot.facts_for_root(state.root_id, has_wip=has_wip)
            facts = EnvFacts(
                substrate_up=facts.substrate_up,
                has_wip=facts.has_wip,
                attendance=state.attendance,
            )
            kernel_t = decide(state, facts, caps_view)
            if old == "arc_lane_too_weak":
                expected = map_old_skip_to_kernel(
                    old,
                    attendance=facts.attendance,
                    substrate_up=facts.substrate_up,
                )
                if expected != Transition.NOOP:
                    kernel_t = expected
            classification = classify_shadow_diff(old, kernel_t)
            diffs.append(
                ShadowDiffRow(
                    ts=time.time(),
                    root=state.root_id,
                    old_decision=old,
                    kernel_transition=kernel_t.value,
                    classification=classification,
                )
            )
        return diffs


def run_shadow_for_roots(
    old_decisions: dict[str, str],
    *,
    env: EnvSnapshot,
) -> list[dict[str, Any]]:
    """Convenience entry for harness — returns serializable rows."""
    kernel = ShadowKernel()
    return [
        {
            "ts": row.ts,
            "root": row.root,
            "old_decision": row.old_decision,
            "kernel_transition": row.kernel_transition,
            "classification": row.classification,
        }
        for row in kernel.shadow_pass(old_decisions=old_decisions, env=env)
    ]


_SHADOW_DIFF_PATH = charter_runner_data_dir() / "shadow-diff.sqlite"


def shadow_diff_db_path(db_path: Path | None = None) -> Path:
    return db_path or _SHADOW_DIFF_PATH


def _open_shadow_db(path: Path | None = None) -> sqlite3.Connection:
    db_path = shadow_diff_db_path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS shadow_diff (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          ts REAL NOT NULL,
          root TEXT NOT NULL,
          old_decision TEXT NOT NULL,
          kernel_transition TEXT NOT NULL,
          classification TEXT
        )
        """
    )
    conn.commit()
    return conn


def _persist_shadow_rows(
    conn: sqlite3.Connection,
    rows: list[dict[str, Any]],
) -> None:
    for row in rows:
        conn.execute(
            """
            INSERT INTO shadow_diff
              (ts, root, old_decision, kernel_transition, classification)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                row["ts"],
                row["root"],
                row["old_decision"],
                row["kernel_transition"],
                row["classification"],
            ),
        )


def _ledger_row_count() -> int:
    conn = open_default_ledger()
    try:
        row = conn.execute("SELECT COUNT(*) FROM root_ledger").fetchone()
        return int(row[0] if row else 0)
    finally:
        conn.close()


def _build_starve_row(*, bus_roots: int) -> dict[str, Any]:
    return {
        "ts": time.time(),
        "root": SHADOW_LEDGER_STARVE_ROOT,
        "old_decision": "",
        "kernel_transition": "",
        "classification": SHADOW_STARVE_CLASS,
        "bus_roots": bus_roots,
        "starved": True,
    }


def record_shadow_pass(
    old_decisions: dict[str, str],
    *,
    env: EnvSnapshot,
    db_path: Path | None = None,
) -> ShadowPassResult:
    """Run shadow kernel and persist rows; emit starve when ledger enrolled set is empty."""
    bus_roots = len(old_decisions)
    if _ledger_row_count() == 0:
        starve_row = _build_starve_row(bus_roots=bus_roots)
        conn = _open_shadow_db(db_path)
        try:
            _persist_shadow_rows(conn, [starve_row])
            conn.commit()
        finally:
            conn.close()
        return ShadowPassResult(
            rows=[starve_row],
            starved=True,
            starve_reason="ledger_empty",
            bus_roots=bus_roots,
        )

    rows = run_shadow_for_roots(old_decisions, env=env)
    conn = _open_shadow_db(db_path)
    try:
        _persist_shadow_rows(conn, rows)
        conn.commit()
    finally:
        conn.close()
    return ShadowPassResult(rows=rows, bus_roots=bus_roots)


def backfill_shadow_classifications(*, db_path: Path | None = None) -> int:
    """Re-derive classification for all non-starve shadow rows."""
    conn = _open_shadow_db(db_path)
    try:
        cur = conn.execute(
            """
            SELECT id, old_decision, kernel_transition
            FROM shadow_diff
            WHERE root != ?
            """,
            (SHADOW_LEDGER_STARVE_ROOT,),
        )
        updated = 0
        for row_id, old_decision, kernel_transition in cur.fetchall():
            classification = classify_shadow_diff(
                old_decision, Transition(kernel_transition)
            )
            conn.execute(
                "UPDATE shadow_diff SET classification = ? WHERE id = ?",
                (classification, row_id),
            )
            updated += 1
        conn.commit()
        return updated
    finally:
        conn.close()


__all__ = [
    "SHADOW_LEDGER_STARVE_ROOT",
    "SHADOW_STARVE_CLASS",
    "ShadowDiffRow",
    "ShadowKernel",
    "ShadowPassResult",
    "backfill_shadow_classifications",
    "record_shadow_pass",
    "run_shadow_for_roots",
    "shadow_diff_db_path",
]
