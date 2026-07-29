"""Ledger-resident age clocks — sole writer via root-ledger sqlite (M4)."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from libs.charter_runner_store.db import (
    charter_runner_data_dir,
    default_ledger_path,
    execute_with_retry,
    open_ledger_db,
)

AgeClass = Literal["tick_stall", "belt_orphan"]

TICK_STALL_MAX_AGE_S = float(os.environ.get("CHARTER_GATE_DEFER_MAX_AGE_S", str(45 * 60)))
BELT_ORPHAN_MAX_AGE_S = 600.0
BELT_ORPHAN_REPEAT_ESCALATE = 3


@dataclass(frozen=True, slots=True)
class AgeWatchResult:
    outcome: Literal["none", "act", "escalate"]
    age_s: float
    first_seen_at: float | None
    observation_count: int


def _ledger_path(*, data_dir: Path | None = None) -> Path:
    if data_dir is not None:
        return data_dir / "root-ledger.sqlite"
    return default_ledger_path()


def _open(*, data_dir: Path | None = None):
    return open_ledger_db(_ledger_path(data_dir=data_dir))


def _refuse_root(key: str) -> tuple[str, bool]:
    if key.endswith(":refuse"):
        return key[: -len(":refuse")], True
    return key, False


def _ensure_root_row(conn, root_id: str) -> None:
    now = time.time()
    execute_with_retry(
        conn,
        """
        INSERT OR IGNORE INTO root_ledger (
          root_id, schema_version, status, attendance, scoreboard_uri, updated_at
        ) VALUES (?, 1, 'IDLE', 'autonomous', '', ?)
        """,
        (root_id, now),
    )


def observation_count(
    cls: AgeClass,
    key: str,
    *,
    data_dir: Path | None = None,
) -> int:
    """Return durable observation count for a class/key (0 when absent)."""
    if cls == "belt_orphan":
        conn = _open(data_dir=data_dir)
        try:
            row = conn.execute(
                """
                SELECT observation_count FROM age_clock
                WHERE clock_class = ? AND clock_key = ?
                """,
                (cls, key),
            ).fetchone()
            return int(row[0]) if row is not None else 0
        finally:
            conn.close()

    root_id, is_refuse = _refuse_root(key)
    conn = _open(data_dir=data_dir)
    try:
        col = "refuse_streak" if is_refuse else "demand_observation_count"
        row = conn.execute(
            f"SELECT {col} FROM root_ledger WHERE root_id = ?",
            (root_id,),
        ).fetchone()
        if row is None:
            return 0
        return int(row[0] or 0)
    finally:
        conn.close()


def first_seen_at(
    cls: AgeClass,
    key: str,
    *,
    data_dir: Path | None = None,
) -> float | None:
    """Durable first-seen timestamp for tick_stall keys."""
    if cls == "belt_orphan":
        conn = _open(data_dir=data_dir)
        try:
            row = conn.execute(
                """
                SELECT first_seen_at FROM age_clock
                WHERE clock_class = ? AND clock_key = ?
                """,
                (cls, key),
            ).fetchone()
            if row is None or row[0] is None:
                return None
            return float(row[0])
        finally:
            conn.close()

    root_id, is_refuse = _refuse_root(key)
    conn = _open(data_dir=data_dir)
    try:
        col = "first_refuse_at" if is_refuse else "demand_since"
        row = conn.execute(
            f"SELECT {col} FROM root_ledger WHERE root_id = ?",
            (root_id,),
        ).fetchone()
        if row is None or row[0] is None:
            return None
        return float(row[0])
    finally:
        conn.close()


def age_s(
    cls: AgeClass,
    key: str,
    *,
    birth: float | None = None,
    now: float | None = None,
    data_dir: Path | None = None,
) -> float:
    """Wall age in seconds; ``birth`` overrides the store for belt_orphan."""
    now = time.time() if now is None else now
    if birth is not None:
        return max(0.0, now - birth)
    seen = first_seen_at(cls, key, data_dir=data_dir)
    if seen is None:
        return 0.0
    return max(0.0, now - seen)


def clear(
    cls: AgeClass,
    key: str,
    *,
    data_dir: Path | None = None,
) -> None:
    """Drop durable observation state for one key."""
    if cls == "belt_orphan":
        conn = _open(data_dir=data_dir)
        try:
            execute_with_retry(
                conn,
                "DELETE FROM age_clock WHERE clock_class = ? AND clock_key = ?",
                (cls, key),
            )
        finally:
            conn.close()
        return

    root_id, is_refuse = _refuse_root(key)
    conn = _open(data_dir=data_dir)
    try:
        if is_refuse:
            execute_with_retry(
                conn,
                """
                UPDATE root_ledger
                SET first_refuse_at = NULL, refuse_streak = 0
                WHERE root_id = ?
                """,
                (root_id,),
            )
        else:
            execute_with_retry(
                conn,
                """
                UPDATE root_ledger
                SET demand_since = NULL, demand_observation_count = 0
                WHERE root_id = ?
                """,
                (root_id,),
            )
    finally:
        conn.close()


def observe(
    cls: AgeClass,
    key: str,
    *,
    present: bool,
    birth: float | None = None,
    now: float | None = None,
    data_dir: Path | None = None,
) -> AgeWatchResult:
    """Record one observation; return act/escalate when bounds exceeded."""
    now = time.time() if now is None else now
    if not present:
        clear(cls, key, data_dir=data_dir)
        return AgeWatchResult(
            outcome="none",
            age_s=0.0,
            first_seen_at=None,
            observation_count=0,
        )

    if cls == "belt_orphan":
        return _observe_belt_orphan(key, birth=birth, now=now, data_dir=data_dir)

    root_id, is_refuse = _refuse_root(key)
    conn = _open(data_dir=data_dir)
    try:
        _ensure_root_row(conn, root_id)
        if is_refuse:
            row = conn.execute(
                """
                SELECT first_refuse_at, refuse_streak FROM root_ledger
                WHERE root_id = ?
                """,
                (root_id,),
            ).fetchone()
            first_raw = row[0] if row is not None else None
            count = int(row[1] or 0) if row is not None else 0
            first = float(first_raw) if first_raw is not None else now
            count += 1
            execute_with_retry(
                conn,
                """
                UPDATE root_ledger
                SET first_refuse_at = ?, refuse_streak = ?
                WHERE root_id = ?
                """,
                (first, count, root_id),
            )
        else:
            row = conn.execute(
                """
                SELECT demand_since, demand_observation_count FROM root_ledger
                WHERE root_id = ?
                """,
                (root_id,),
            ).fetchone()
            first_raw = row[0] if row is not None else None
            count = int(row[1] or 0) if row is not None else 0
            first = float(first_raw) if first_raw is not None else now
            count += 1
            execute_with_retry(
                conn,
                """
                UPDATE root_ledger
                SET demand_since = ?, demand_observation_count = ?
                WHERE root_id = ?
                """,
                (first, count, root_id),
            )

        current_age = max(0.0, now - first)
        max_age = TICK_STALL_MAX_AGE_S
        if current_age >= max_age:
            return AgeWatchResult(
                outcome="escalate",
                age_s=current_age,
                first_seen_at=first,
                observation_count=count,
            )
        return AgeWatchResult(
            outcome="none",
            age_s=current_age,
            first_seen_at=first,
            observation_count=count,
        )
    finally:
        conn.close()


def _observe_belt_orphan(
    key: str,
    *,
    birth: float | None,
    now: float,
    data_dir: Path | None,
) -> AgeWatchResult:
    conn = _open(data_dir=data_dir)
    try:
        row = conn.execute(
            """
            SELECT first_seen_at, observation_count, birth FROM age_clock
            WHERE clock_class = 'belt_orphan' AND clock_key = ?
            """,
            (key,),
        ).fetchone()
        if birth is not None:
            first = float(birth)
        else:
            first_raw = row[0] if row is not None else None
            first = float(first_raw) if first_raw is not None else now
        count = int(row[1] or 0) if row is not None else 0
        count += 1
        execute_with_retry(
            conn,
            """
            INSERT INTO age_clock (
              clock_class, clock_key, first_seen_at, observation_count, birth
            ) VALUES ('belt_orphan', ?, ?, ?, ?)
            ON CONFLICT(clock_class, clock_key) DO UPDATE SET
              first_seen_at = excluded.first_seen_at,
              observation_count = excluded.observation_count,
              birth = excluded.birth
            """,
            (key, first, count, birth),
        )
        current_age = age_s("belt_orphan", key, birth=birth, now=now, data_dir=data_dir)
        if count >= BELT_ORPHAN_REPEAT_ESCALATE:
            return AgeWatchResult(
                outcome="escalate",
                age_s=current_age,
                first_seen_at=first,
                observation_count=count,
            )
        if current_age >= BELT_ORPHAN_MAX_AGE_S:
            return AgeWatchResult(
                outcome="act",
                age_s=current_age,
                first_seen_at=first,
                observation_count=count,
            )
        return AgeWatchResult(
            outcome="none",
            age_s=current_age,
            first_seen_at=first,
            observation_count=count,
        )
    finally:
        conn.close()


def seed_first_seen(
    cls: AgeClass,
    key: str,
    first_seen_at_ts: float,
    *,
    observation_count: int = 1,
    data_dir: Path | None = None,
) -> None:
    """Seed durable clock state (tests + gate-defer record with explicit ``now``)."""
    if cls == "belt_orphan":
        conn = _open(data_dir=data_dir)
        try:
            execute_with_retry(
                conn,
                """
                INSERT INTO age_clock (
                  clock_class, clock_key, first_seen_at, observation_count
                ) VALUES ('belt_orphan', ?, ?, ?)
                ON CONFLICT(clock_class, clock_key) DO UPDATE SET
                  first_seen_at = excluded.first_seen_at,
                  observation_count = excluded.observation_count
                """,
                (key, float(first_seen_at_ts), int(observation_count)),
            )
        finally:
            conn.close()
        return

    root_id, is_refuse = _refuse_root(key)
    conn = _open(data_dir=data_dir)
    try:
        _ensure_root_row(conn, root_id)
        if is_refuse:
            execute_with_retry(
                conn,
                """
                UPDATE root_ledger
                SET first_refuse_at = ?, refuse_streak = ?
                WHERE root_id = ?
                """,
                (float(first_seen_at_ts), int(observation_count), root_id),
            )
        else:
            execute_with_retry(
                conn,
                """
                UPDATE root_ledger
                SET demand_since = ?, demand_observation_count = ?
                WHERE root_id = ?
                """,
                (float(first_seen_at_ts), int(observation_count), root_id),
            )
    finally:
        conn.close()


__all__ = [
    "AgeClass",
    "AgeWatchResult",
    "BELT_ORPHAN_MAX_AGE_S",
    "BELT_ORPHAN_REPEAT_ESCALATE",
    "TICK_STALL_MAX_AGE_S",
    "age_s",
    "clear",
    "first_seen_at",
    "observe",
    "observation_count",
    "seed_first_seen",
]
