"""Resume fence journal — append-only authority folded to fence state."""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from .db.connection import connect
from .events.resume_fence import (
    emit_resume_fence_armed,
    emit_resume_fence_denied,
    emit_resume_fence_expired,
    emit_resume_fence_poured,
    emit_resume_fence_released,
)

FenceStateName = Literal["armed", "poured", "released", "expired"]

_TERMINAL: frozenset[str] = frozenset({"released", "expired"})
_OPEN: frozenset[str] = frozenset({"armed", "poured"})

RESUME_FENCE_IDLE_S = int(os.environ.get("RESUME_FENCE_IDLE_S", "1800"))
RESUME_FENCE_ADOPT_S = int(os.environ.get("RESUME_FENCE_ADOPT_S", "120"))
_HOOK_ADOPT_SOURCES = frozenset({"hook_prompt", "hook_session_start"})


@dataclass(frozen=True, slots=True)
class FenceState:
    """Folded fence state from the append-only journal."""

    fence_id: str
    root_thread: str
    transcript_id: str | None
    state: FenceStateName
    last_event_at: str | None = None
    payload: dict[str, Any] | None = None


def mint_fence_id() -> str:
    """Return a new ``rf-<uuid8>`` fence identifier."""
    return f"rf-{uuid.uuid4().hex[:8]}"


def append_fence_event(
    *,
    fence_id: str,
    root_thread: str,
    event: str,
    transcript_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> int:
    """Append one journal row and emit the matching event signal."""
    payload_json = json.dumps(payload) if payload else None
    with connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO resume_fence_events
                (fence_id, root_thread, transcript_id, event, payload_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (fence_id, root_thread, transcript_id, event, payload_json),
        )
        row_id = int(cur.lastrowid)

    if event == "armed":
        emit_resume_fence_armed(
            fence_id=fence_id,
            root_thread=root_thread,
            transcript_id=transcript_id,
            source=(payload or {}).get("source", "mcp"),
        )
    elif event == "poured":
        emit_resume_fence_poured(
            fence_id=fence_id,
            root_thread=root_thread,
            bundle_bytes=int((payload or {}).get("bundle_bytes", 0)),
            readable_counts=(payload or {}).get("readable_counts") or {},
            seal_status=str((payload or {}).get("seal_status", "")),
        )
    elif event == "denied":
        emit_resume_fence_denied(
            fence_id=fence_id,
            surface=str((payload or {}).get("surface", "")),
            tool=str((payload or {}).get("tool", "")),
            op=str((payload or {}).get("op", "")),
            target=str((payload or {}).get("target", "")),
            reason=str((payload or {}).get("reason", "")),
        )
    elif event == "released":
        emit_resume_fence_released(
            fence_id=fence_id,
            release_turn=int((payload or {}).get("release_turn", 0)),
        )
    elif event == "expired":
        emit_resume_fence_expired(
            fence_id=fence_id,
            idle_seconds=int((payload or {}).get("idle_seconds", RESUME_FENCE_IDLE_S)),
        )
    return row_id


def _rows_for_fence(fence_id: str) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT fence_id, root_thread, transcript_id, event, payload_json, created_at
            FROM resume_fence_events
            WHERE fence_id = ?
            ORDER BY id ASC
            """,
            (fence_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def fold_fence(fence_id: str) -> FenceState | None:
    """Fold journal rows for *fence_id* into current state."""
    rows = _rows_for_fence(fence_id)
    if not rows:
        return None
    state: FenceStateName = "armed"
    last_at: str | None = None
    transcript_id = rows[0].get("transcript_id")
    root_thread = str(rows[0]["root_thread"])
    for row in rows:
        event = str(row["event"])
        last_at = str(row["created_at"])
        if event in _TERMINAL:
            state = event  # type: ignore[assignment]
        elif event == "poured":
            state = "poured"
        elif event == "armed":
            state = "armed"
        if row.get("transcript_id"):
            transcript_id = row["transcript_id"]
    return FenceState(
        fence_id=fence_id,
        root_thread=root_thread,
        transcript_id=transcript_id,
        state=state,
        last_event_at=last_at,
    )


def _armed_source(fence_id: str) -> str | None:
    for row in _rows_for_fence(fence_id):
        if str(row["event"]) != "armed":
            continue
        raw = row.get("payload_json")
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        source = payload.get("source")
        return str(source) if source else None
    return None


def _within_adopt_window(created_at: str | None) -> bool:
    if not created_at:
        return False
    try:
        created = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
    except ValueError:
        return False
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    idle_s = int((datetime.now(UTC) - created).total_seconds())
    return idle_s <= RESUME_FENCE_ADOPT_S


def _null_armed_fences(root_thread: str) -> list[str]:
    """Armed NULL-transcript fences on *root_thread* inside the adoption window."""
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT fence_id, created_at
            FROM resume_fence_events
            WHERE root_thread = ? AND transcript_id IS NULL AND event = 'armed'
            ORDER BY id DESC
            LIMIT 200
            """,
            (root_thread,),
        ).fetchall()
    candidates: list[str] = []
    seen: set[str] = set()
    for row in rows:
        fid = str(row["fence_id"])
        if fid in seen:
            continue
        seen.add(fid)
        folded = fold_fence(fid)
        if folded is None or folded.state != "armed":
            continue
        if not _within_adopt_window(str(row["created_at"])):
            continue
        candidates.append(fid)
    return candidates


def _sweep_legacy_poured_null_fences(root_thread: str) -> None:
    """Release legacy poured NULL-transcript fences left open before FIX-13."""
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT fence_id FROM resume_fence_events
            WHERE root_thread = ? AND transcript_id IS NULL
            ORDER BY id DESC
            LIMIT 20
            """,
            (root_thread,),
        ).fetchall()
    for row in rows:
        fid = str(row["fence_id"])
        folded = fold_fence(fid)
        if folded is None or folded.state != "poured":
            continue
        append_fence_event(
            fence_id=fid,
            root_thread=folded.root_thread,
            transcript_id=folded.transcript_id,
            event="released",
            payload={"release_reason": "pour_terminal_sweep", "release_turn": 0},
        )


def _adoptable_armed_fences(root_thread: str) -> list[str]:
    """Armed hook fences on *root_thread* inside the adoption window."""
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT fence_id, created_at
            FROM resume_fence_events
            WHERE root_thread = ? AND event = 'armed'
            ORDER BY id DESC
            LIMIT 200
            """,
            (root_thread,),
        ).fetchall()
    candidates: list[str] = []
    seen: set[str] = set()
    for row in rows:
        fid = str(row["fence_id"])
        if fid in seen:
            continue
        seen.add(fid)
        folded = fold_fence(fid)
        if folded is None or folded.state != "armed":
            continue
        source = _armed_source(fid)
        if source not in _HOOK_ADOPT_SOURCES:
            continue
        if not _within_adopt_window(str(row["created_at"])):
            continue
        candidates.append(fid)
    return candidates


def adoption_ambiguous_count(root_thread: str) -> int:
    """Count adoptable armed hook fences (0, 1, or >1)."""
    return len(_adoptable_armed_fences(root_thread))


def read_set_from_journal(fence_id: str) -> dict[str, Any] | None:
    """Return ``read_set`` from the latest armed/poured payload, if present."""
    for row in reversed(_rows_for_fence(fence_id)):
        if str(row["event"]) not in {"armed", "poured"}:
            continue
        raw = row.get("payload_json")
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        read_set = payload.get("read_set")
        if isinstance(read_set, dict):
            return read_set
    return None


def find_open_fence(
    *,
    root_thread: str,
    transcript_id: str | None,
) -> str | None:
    """Return open ``fence_id`` for *(root_thread, transcript_id)* if any.

    When *transcript_id* is None (seat pour path): hook armed ≤120s first,
    then a single NULL-transcript armed fence ≤120s. Legacy poured NULL fences
    are swept to released before lookup (FIX-13/14).
    """
    if transcript_id is None:
        _sweep_legacy_poured_null_fences(root_thread)
        adoptable = _adoptable_armed_fences(root_thread)
        if len(adoptable) == 1:
            return adoptable[0]
        null_armed = _null_armed_fences(root_thread)
        if len(null_armed) == 1:
            return null_armed[0]
        return None

    with connect() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT fence_id FROM resume_fence_events
            WHERE root_thread = ? AND transcript_id = ?
            ORDER BY id DESC
            LIMIT 5
            """,
            (root_thread, transcript_id),
        ).fetchall()
    for row in rows:
        folded = fold_fence(str(row["fence_id"]))
        if folded and folded.state in _OPEN:
            return folded.fence_id
    return None


def find_open_fence_for_agent(
    *,
    root_thread: str,
    from_agent: str,
) -> str | None:
    """Return open fence when *from_agent* matches journaled transcript binding."""
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT fence_id, transcript_id, event, payload_json
            FROM resume_fence_events
            WHERE root_thread = ?
            ORDER BY id DESC
            LIMIT 200
            """,
            (root_thread,),
        ).fetchall()
    seen: set[str] = set()
    for row in rows:
        fid = str(row["fence_id"])
        if fid in seen:
            continue
        seen.add(fid)
        folded = fold_fence(fid)
        if folded is None or folded.state not in _OPEN:
            continue
        payload_raw = row["payload_json"]
        if payload_raw:
            try:
                payload = json.loads(payload_raw)
            except json.JSONDecodeError:
                payload = {}
            bound_agent = payload.get("from_agent")
            if bound_agent and bound_agent == from_agent:
                return fid
        if folded.transcript_id and from_agent.endswith(folded.transcript_id[:8]):
            return fid
    return None


def journal_denied(
    *,
    fence_id: str,
    surface: str,
    tool: str,
    op: str,
    target: str,
    reason: str,
) -> None:
    """Append a hook deny row (does not change fold state)."""
    folded = fold_fence(fence_id)
    if folded is None:
        return
    append_fence_event(
        fence_id=fence_id,
        root_thread=folded.root_thread,
        transcript_id=folded.transcript_id,
        event="denied",
        payload={
            "surface": surface,
            "tool": tool,
            "op": op,
            "target": target,
            "reason": reason,
        },
    )


def maybe_expire_idle_fence(fence_id: str) -> FenceState | None:
    """Fold *fence_id* and journal ``expired`` when idle past ``RESUME_FENCE_IDLE_S``."""
    folded = fold_fence(fence_id)
    if folded is None or folded.state not in _OPEN:
        return folded
    if not folded.last_event_at:
        return folded
    last_raw = str(folded.last_event_at).replace("Z", "+00:00")
    try:
        last_at = datetime.fromisoformat(last_raw)
    except ValueError:
        return folded
    if last_at.tzinfo is None:
        last_at = last_at.replace(tzinfo=UTC)
    idle_s = int((datetime.now(UTC) - last_at).total_seconds())
    if idle_s <= RESUME_FENCE_IDLE_S:
        return folded
    append_fence_event(
        fence_id=fence_id,
        root_thread=folded.root_thread,
        transcript_id=folded.transcript_id,
        event="expired",
        payload={"idle_seconds": idle_s},
    )
    return fold_fence(fence_id)


def release_fence(
    *,
    fence_id: str,
    release_turn: int = 0,
    release_reason: str | None = None,
) -> FenceState | None:
    """Journal explicit release."""
    folded = fold_fence(fence_id)
    if folded is None or folded.state not in _OPEN:
        return folded
    payload: dict[str, Any] = {"release_turn": release_turn}
    if release_reason:
        payload["release_reason"] = release_reason
    append_fence_event(
        fence_id=fence_id,
        root_thread=folded.root_thread,
        transcript_id=folded.transcript_id,
        event="released",
        payload=payload,
    )
    return fold_fence(fence_id)


def pour_terminal_release(*, fence_id: str) -> FenceState | None:
    """Journal pour-terminal release (FIX-13): poured bundle closes the gate."""
    folded = fold_fence(fence_id)
    if folded is None:
        return None
    if folded.state == "released":
        return folded
    if folded.state not in _OPEN:
        return folded
    return release_fence(
        fence_id=fence_id,
        release_turn=0,
        release_reason="pour_terminal",
    )


__all__ = [
    "FenceState",
    "RESUME_FENCE_ADOPT_S",
    "RESUME_FENCE_IDLE_S",
    "adoption_ambiguous_count",
    "append_fence_event",
    "find_open_fence",
    "find_open_fence_for_agent",
    "fold_fence",
    "journal_denied",
    "maybe_expire_idle_fence",
    "mint_fence_id",
    "pour_terminal_release",
    "read_set_from_journal",
    "release_fence",
]
