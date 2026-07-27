"""Shadow-mode charter kernel — decide only; old tick remains sole admitter."""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .admission import CapsView, EnvFacts, classify_shadow_diff, decide, map_old_skip_to_kernel
from .caps import CapStore
from .env_snapshot import EnvSnapshot, build_env_snapshot
from .root_ledger import RootLedgerRow, Transition, load_all_roots, open_default_ledger


@dataclass(frozen=True)
class ShadowDiffRow:
    ts: float
    root: str
    old_decision: str
    kernel_transition: str
    classification: str | None


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
        env: EnvSnapshot | None = None,
    ) -> list[ShadowDiffRow]:
        """Run decide for each ledger root; compare to old tick outcomes."""
        conn = open_default_ledger()
        try:
            rows = load_all_roots(conn)
        finally:
            conn.close()
        if not rows:
            return []
        root_ids = [r.root_id for r in rows]
        snapshot = env or build_env_snapshot(root_ids=root_ids)
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
    env: EnvSnapshot | None = None,
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


_SHADOW_DIFF_PATH = (
    Path.home() / ".local" / "share" / "charter-runner" / "shadow-diff.sqlite"
)


def _open_shadow_db(path: Path | None = None) -> sqlite3.Connection:
    db_path = path or _SHADOW_DIFF_PATH
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


def record_shadow_pass(
    old_decisions: dict[str, str],
    *,
    db_path: Path | None = None,
) -> list[dict[str, Any]]:
    """Run shadow kernel and persist rows; return inserted records."""
    rows = run_shadow_for_roots(old_decisions)
    conn = _open_shadow_db(db_path)
    try:
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
        conn.commit()
    finally:
        conn.close()
    return rows


__all__ = [
    "ShadowDiffRow",
    "ShadowKernel",
    "record_shadow_pass",
    "run_shadow_for_roots",
]
