"""Open-park projection for conductor census rows (fork C6)."""

from __future__ import annotations

import sqlite3
from dataclasses import replace

from services.git_integration_worker.cursor_sdk_closeout.conductor_census import (
    MissionCensusRow,
)


def apply_open_park_projection(
    conn: sqlite3.Connection,
    entry: MissionCensusRow,
    *,
    latest_ids: frozenset[str],
    now_iso: str,
) -> tuple[MissionCensusRow, bool]:
    """Augment a census row with open-park scope; return (row, has_hidden_parks)."""
    from services.git_integration_worker.cursor_sdk_conductor_park_gate import (
        open_parks,
    )

    work_key = entry.work_key or None
    thread_id = entry.thread_id or None
    try:
        members = open_parks(
            conn,
            work_key=work_key,
            thread_id=thread_id,
            now=now_iso,
        )
    except Exception as exc:  # noqa: BLE001 — census must not die on one mission
        return (
            replace(entry, reason=f"{entry.reason}; open-park read failed: {exc}"),
            False,
        )
    hidden = [p for p in members if p.dispatch_id not in latest_ids]
    if not hidden:
        return entry, False
    hidden_ids = ", ".join(p.dispatch_id for p in hidden)
    reason = (
        f"{entry.reason}; open parks in mission scope: {len(members)} "
        f"(hidden: {hidden_ids})"
    )
    note_parts: list[str] = []
    hidden_budget = [p.dispatch_id for p in hidden if p.kind == "budget"]
    if hidden_budget:
        ids = ", ".join(hidden_budget)
        note_parts.append(
            f'next conductor admit must set generation_options={{"hop_park_release": true}} '
            f"(hidden budget parks: {ids})"
        )
    for p in hidden:
        if p.kind != "restart":
            continue
        wk = p.work_key or work_key or "?"
        note_parts.append(
            f'restart park {p.dispatch_id} holds work key {wk} until resume_of="{p.dispatch_id}" '
            f"admits or park_expires_at passes"
        )
    note = "; ".join(note_parts)
    release = entry.release
    if release == "none":
        release = note
    else:
        release = f"{release}; {note}"
    return (
        replace(entry, stacked_parks=len(members), reason=reason, release=release),
        True,
    )


__all__ = ["apply_open_park_projection"]
