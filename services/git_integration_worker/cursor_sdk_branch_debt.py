"""Branch debt ledger — carries the Lane-B unlanded grade past the closeout.

``cursor_sdk_land_discipline`` already downgrades an unlanded Lane-B closeout to
``partial`` and stamps ``land:lane_b_unlanded``. That grade previously lived in a
single closeout payload and evaporated: no owner, no durable record, no prompt to
resolve it, so the branch outlived every sweep with nothing attached to it.

A debt row is that missing carrier. It names who left the residue (thread,
dispatch, caller) and survives until an explicit discharge retires it.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from services.git_integration_worker.cursor_dispatch_ledger import _connect

_DEBT_DDL = """
CREATE TABLE IF NOT EXISTS cursor_sdk_branch_debts (
    branch_name    TEXT PRIMARY KEY,
    thread_id      TEXT,
    dispatch_id    TEXT,
    caller_agent   TEXT,
    tip_sha        TEXT,
    files_json     TEXT,
    opened_at      TEXT NOT NULL,
    escalated_at   TEXT,
    discharged_at  TEXT,
    discharge_verb TEXT,
    discharge_note TEXT,
    source_repo    TEXT
);
"""

_DEBT_COLUMN_MIGRATIONS = (
    ("source_repo", "TEXT"),
)


@dataclass(frozen=True, slots=True)
class BranchDebt:
    """An unlanded Lane-B branch with the lane that owes it."""

    branch_name: str
    thread_id: str | None
    dispatch_id: str | None
    caller_agent: str | None
    tip_sha: str | None
    files: list[str]
    opened_at: str
    escalated_at: str | None = None
    discharged_at: str | None = None
    discharge_verb: str | None = None
    discharge_note: str | None = None
    source_repo: str | None = None

    @property
    def open(self) -> bool:
        """True while no discharge has retired this debt."""
        return self.discharged_at is None

    def age_s(self, *, now: datetime | None = None) -> float | None:
        """Seconds since the debt opened, or ``None`` when unparseable."""
        return _age_s(self.opened_at, now=now)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _age_s(stamp: str | None, *, now: datetime | None = None) -> float | None:
    if not stamp:
        return None
    try:
        opened = datetime.fromisoformat(stamp)
    except ValueError:
        return None
    if opened.tzinfo is None:
        opened = opened.replace(tzinfo=UTC)
    return max(0.0, ((now or datetime.now(UTC)) - opened).total_seconds())


def ensure_debt_schema(conn: sqlite3.Connection) -> None:
    """Create the debt table when missing."""
    conn.executescript(_DEBT_DDL)
    cols = {
        row[1] for row in conn.execute("PRAGMA table_info(cursor_sdk_branch_debts)")
    }
    for name, decl in _DEBT_COLUMN_MIGRATIONS:
        if name not in cols:
            try:
                conn.execute(
                    f"ALTER TABLE cursor_sdk_branch_debts ADD COLUMN {name} {decl}"
                )
            except sqlite3.OperationalError as exc:
                if "duplicate column" not in str(exc).lower():
                    raise


def _row_to_debt(row: sqlite3.Row) -> BranchDebt:
    raw_files = row["files_json"]
    try:
        files = list(json.loads(raw_files)) if raw_files else []
    except (TypeError, ValueError):
        files = []
    keys = row.keys()
    source_repo = row["source_repo"] if "source_repo" in keys else None
    return BranchDebt(
        branch_name=row["branch_name"],
        thread_id=row["thread_id"],
        dispatch_id=row["dispatch_id"],
        caller_agent=row["caller_agent"],
        tip_sha=row["tip_sha"],
        files=files,
        opened_at=row["opened_at"],
        escalated_at=row["escalated_at"],
        discharged_at=row["discharged_at"],
        discharge_verb=row["discharge_verb"],
        discharge_note=row["discharge_note"],
        source_repo=source_repo,
    )


def workspace_token_for_repo(
    source_repo: Path,
    *,
    hub: Path,
    projects_root: Path,
) -> str | None:
    """Return allowlist workspace name for *source_repo*, or ``None`` for hub."""
    from services.git_integration_worker.cursor_sdk_satellite_workspace import (
        load_satellite_allowlist,
    )

    resolved = source_repo.resolve()
    if resolved == hub.resolve():
        return None
    allowlist = load_satellite_allowlist(hub=hub)
    for name in allowlist:
        if (projects_root / name).resolve() == resolved:
            return name
    return str(resolved)


def resolve_debt_source_repo(
    stored: str | None,
    *,
    hub: Path,
    projects_root: Path,
) -> Path:
    """Resolve a debt row's stored workspace token back to a git repo root."""
    from services.git_integration_worker.cursor_sdk_satellite_workspace import (
        resolve_dispatch_source_repo,
    )

    if not stored or not str(stored).strip():
        return hub.resolve()
    token = str(stored).strip()
    if token.startswith("/") or "\\" in token:
        return Path(token).resolve()
    return resolve_dispatch_source_repo(
        token,
        hub=hub,
        projects_root=projects_root,
    )


def open_branch_debt(
    *,
    branch_name: str,
    thread_id: str | None = None,
    dispatch_id: str | None = None,
    caller_agent: str | None = None,
    tip_sha: str | None = None,
    files: list[str] | None = None,
    source_repo: str | None = None,
) -> BranchDebt:
    """Open a debt for *branch_name*, or return the existing open one.

    Re-terminal on the same lane must not reset the clock: an already-open debt
    keeps its original ``opened_at`` so age escalation reflects when the residue
    first appeared, not when it was last observed.
    """
    existing = get_branch_debt(branch_name=branch_name)
    if existing is not None and existing.open:
        return existing
    opened_at = _now()
    with _connect() as conn:
        ensure_debt_schema(conn)
        conn.execute(
            "INSERT OR REPLACE INTO cursor_sdk_branch_debts "
            "(branch_name, thread_id, dispatch_id, caller_agent, tip_sha, "
            "files_json, opened_at, escalated_at, discharged_at, discharge_verb, "
            "discharge_note, source_repo) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, ?)",
            (
                branch_name,
                thread_id,
                dispatch_id,
                caller_agent,
                tip_sha,
                json.dumps(sorted(files or [])),
                opened_at,
                source_repo,
            ),
        )
    return BranchDebt(
        branch_name=branch_name,
        thread_id=thread_id,
        dispatch_id=dispatch_id,
        caller_agent=caller_agent,
        tip_sha=tip_sha,
        files=sorted(files or []),
        opened_at=opened_at,
        source_repo=source_repo,
    )


def get_branch_debt(*, branch_name: str) -> BranchDebt | None:
    """Return the debt row for *branch_name*, discharged or not."""
    with _connect() as conn:
        ensure_debt_schema(conn)
        row = conn.execute(
            "SELECT * FROM cursor_sdk_branch_debts WHERE branch_name=?",
            (branch_name,),
        ).fetchone()
    return None if row is None else _row_to_debt(row)


def discharge_branch_debt(
    *,
    branch_name: str,
    verb: str,
    note: str | None = None,
) -> BranchDebt | None:
    """Retire the debt for *branch_name*; returns ``None`` when none was open."""
    debt = get_branch_debt(branch_name=branch_name)
    if debt is None or not debt.open:
        return None
    discharged_at = _now()
    with _connect() as conn:
        ensure_debt_schema(conn)
        conn.execute(
            "UPDATE cursor_sdk_branch_debts SET discharged_at=?, discharge_verb=?, "
            "discharge_note=? WHERE branch_name=?",
            (discharged_at, verb, note, branch_name),
        )
    return BranchDebt(
        branch_name=debt.branch_name,
        thread_id=debt.thread_id,
        dispatch_id=debt.dispatch_id,
        caller_agent=debt.caller_agent,
        tip_sha=debt.tip_sha,
        files=debt.files,
        opened_at=debt.opened_at,
        escalated_at=debt.escalated_at,
        discharged_at=discharged_at,
        discharge_verb=verb,
        discharge_note=note,
    )


def list_open_debts() -> list[BranchDebt]:
    """All open debts, oldest first."""
    with _connect() as conn:
        ensure_debt_schema(conn)
        rows = conn.execute(
            "SELECT * FROM cursor_sdk_branch_debts WHERE discharged_at IS NULL "
            "ORDER BY opened_at"
        ).fetchall()
    return [_row_to_debt(row) for row in rows]


def open_debts_for_thread(thread_id: str) -> list[BranchDebt]:
    """Open debts owed by one lane."""
    if not thread_id:
        return []
    with _connect() as conn:
        ensure_debt_schema(conn)
        rows = conn.execute(
            "SELECT * FROM cursor_sdk_branch_debts WHERE discharged_at IS NULL "
            "AND thread_id=? ORDER BY opened_at",
            (thread_id,),
        ).fetchall()
    return [_row_to_debt(row) for row in rows]


def mark_debt_escalated(*, branch_name: str) -> None:
    """Stamp the escalation clock so aged-debt signalling fires once per debt."""
    with _connect() as conn:
        ensure_debt_schema(conn)
        conn.execute(
            "UPDATE cursor_sdk_branch_debts SET escalated_at=? WHERE branch_name=? "
            "AND discharged_at IS NULL",
            (_now(), branch_name),
        )


def delete_branch_debt(*, branch_name: str) -> None:
    """Drop a debt row outright (registry hygiene, not discharge)."""
    with _connect() as conn:
        ensure_debt_schema(conn)
        conn.execute(
            "DELETE FROM cursor_sdk_branch_debts WHERE branch_name=?",
            (branch_name,),
        )


def lane_hygiene_snapshot(*, now: datetime | None = None) -> dict[str, Any]:
    """Open-debt standing, shaped for the admit response and busy_status.

    Surfaced where a seat decides whether to dispatch again, so the lane's
    outstanding residue is visible at the moment the choice is made.
    """
    debts = list_open_debts()
    per_lane: dict[str, int] = {}
    oldest_age_s: float | None = None
    entries: list[dict[str, Any]] = []
    for debt in debts:
        lane = debt.thread_id or "(unattributed)"
        per_lane[lane] = per_lane.get(lane, 0) + 1
        age_s = debt.age_s(now=now)
        if age_s is not None and (oldest_age_s is None or age_s > oldest_age_s):
            oldest_age_s = age_s
        entries.append(
            {
                "branch": debt.branch_name,
                "thread_id": debt.thread_id,
                "dispatch_id": debt.dispatch_id,
                "caller_agent": debt.caller_agent,
                "tip_sha": debt.tip_sha,
                "source_repo": debt.source_repo,
                "age_s": age_s,
            }
        )
    return {
        "open_debts": len(debts),
        "oldest_debt_age_s": oldest_age_s,
        "debts_by_lane": per_lane,
        "debts": entries,
    }
