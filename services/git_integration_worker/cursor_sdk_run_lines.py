"""Assistant text and thinking kept on the live run while the stream drains.

``observe_run_stream`` used to keep tool calls, usage, and request ids and
drop prose. A reader of the registered live run can take lines after a
cursor without a spool file and without folding the text into a snapshot.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RunLine:
    """One retained prose fragment. ``index`` is the cursor of this line."""

    index: int
    kind: str
    text: str


class RunLineLog:
    """Append-only prose on one live run. Safe for a reader on another thread."""

    def __init__(self) -> None:
        self._lines: list[RunLine] = []
        self._lock = threading.Lock()

    def append(self, kind: str, text: str) -> RunLine:
        with self._lock:
            line = RunLine(index=len(self._lines) + 1, kind=kind, text=text)
            self._lines.append(line)
            return line

    def after(self, cursor: int) -> tuple[tuple[RunLine, ...], int]:
        """Lines with ``index`` greater than ``cursor``, and the new cursor."""
        with self._lock:
            lines = tuple(line for line in self._lines if line.index > cursor)
            if not self._lines:
                return lines, cursor
            new_cursor = self._lines[-1].index
            if new_cursor < cursor:
                new_cursor = cursor
            return lines, new_cursor

    def snapshot(self) -> tuple[RunLine, ...]:
        with self._lock:
            return tuple(self._lines)


_finished_lock = threading.Lock()
_finished: dict[str, RunLineLog] = {}


def park_finished_lines(dispatch_id: str, log: RunLineLog) -> None:
    """Keep the prose log after the live run unregisters so a tail can still read it."""
    with _finished_lock:
        _finished[dispatch_id] = log


def _finished_log(dispatch_id: str) -> RunLineLog | None:
    with _finished_lock:
        return _finished.get(dispatch_id)


def read_run_lines(
    dispatch_id: str, cursor: int = 0
) -> tuple[tuple[RunLine, ...], int]:
    """Prose for ``dispatch_id`` after ``cursor``.

    A live run wins. After unregister, the parked log is still readable.
    An unknown dispatch returns no lines and leaves the cursor unchanged.
    """
    from services.git_integration_worker.cursor_sdk_supersede import (
        live_run_for_dispatch,
    )

    record = live_run_for_dispatch(dispatch_id)
    if record is not None:
        return record.lines.after(cursor)
    finished = _finished_log(dispatch_id)
    if finished is None:
        return (), cursor
    return finished.after(cursor)


def conversation_tail(dispatch_id: str, after: int) -> dict[str, Any]:
    """Tail payload for one cursor-sdk dispatch. ``eof`` is true once it is not live.

    Source is ``sdk.run_lines``: the drain's retained assistant text and thinking,
    not ``run.conversation()``, which drops thinking and may be empty until wait.
    Frontier/api_role rows with no run log fall back to agent-bus turns on the
    ledger thread_id (source agent-bus.turns; after is after_turn).
    """
    from services.git_integration_worker.cursor_sdk_supersede import (
        live_run_for_dispatch,
    )

    record = live_run_for_dispatch(dispatch_id)
    log = record.lines if record is not None else _finished_log(dispatch_id)
    if log is None:
        bus = _bus_conversation_tail(dispatch_id, after)
        if bus is not None:
            return bus
        return {
            "lines": [],
            "cursor": after,
            "eof": True,
            "source": "sdk.run_lines",
        }
    lines, cursor = log.after(after)
    return {
        "lines": [
            {"index": line.index, "kind": line.kind, "text": line.text}
            for line in lines
        ],
        "cursor": cursor,
        "eof": record is None,
        "source": "sdk.run_lines",
    }


_TERMINAL_DISPATCH = frozenset(
    {"completed", "failed", "cancelled", "timeout", "orphaned", "claimed"}
)


def _ledger_row(dispatch_id: str) -> tuple[str | None, str | None]:
    from services.git_integration_worker.cursor_dispatch_ledger import (
        CursorDispatchLedger,
    )

    with CursorDispatchLedger.instance()._connect() as conn:
        row = conn.execute(
            "SELECT thread_id, status FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (dispatch_id,),
        ).fetchone()
    if row is None:
        return None, None
    thread = str(row["thread_id"] or "").strip() or None
    status = str(row["status"] or "").strip().lower() or None
    return thread, status


def _agent_bus_db_path() -> str:
    return os.environ.get(
        "AGENT_BUS_DB_PATH",
        os.path.expanduser("~/.agent-bus/messages.db"),
    )


def _bus_turns_sqlite(thread_id: str, after_turn: int, *, limit: int = 80) -> list[dict]:
    path = _agent_bus_db_path()
    if not os.path.isfile(path):
        return []
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2.0)
        conn.row_factory = sqlite3.Row
    except sqlite3.Error:
        return []
    try:
        rows = conn.execute(
            "SELECT turn_number, from_agent, to_agent, subject, body "
            "FROM turns WHERE thread=? AND turn_number>? "
            "ORDER BY turn_number ASC LIMIT ?",
            (thread_id, after_turn, limit),
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()
    return [
        {
            "turn_number": int(row["turn_number"]),
            "from_agent": row["from_agent"],
            "to": row["to_agent"],
            "subject": row["subject"],
            "body": row["body"],
        }
        for row in rows
    ]


def _fetch_bus_turns(thread_id: str, after_turn: int) -> list[dict]:
    token = os.environ.get("AGENT_BUS_TOKEN", "").strip()
    if token:
        try:
            from services.git_integration_worker.cursor_sdk_await_reply import (
                _default_bus_turns,
            )

            turns = _default_bus_turns(thread_id, after_turn)
            if turns:
                return turns
        except Exception:
            pass
    return _bus_turns_sqlite(thread_id, after_turn)


def _bus_conversation_tail(dispatch_id: str, after_turn: int) -> dict[str, Any] | None:
    thread_id, status = _ledger_row(dispatch_id)
    if not thread_id:
        return None
    turns = _fetch_bus_turns(thread_id, after_turn)
    lines: list[dict[str, Any]] = []
    cursor = after_turn
    for turn in turns:
        if not isinstance(turn, dict):
            continue
        num = turn.get("turn_number")
        if not isinstance(num, int):
            continue
        cursor = max(cursor, num)
        body = turn.get("body")
        body_text = body.strip() if isinstance(body, str) else ""
        subject = turn.get("subject")
        subj = subject.strip() if isinstance(subject, str) else ""
        frm = turn.get("from_agent") or turn.get("from") or "?"
        parts = [f"turn {num} · {frm}"]
        if subj:
            parts.append(subj)
        if body_text:
            parts.append(body_text)
        text = "\n".join(parts)
        if not text.strip():
            continue
        lines.append({"index": num, "kind": str(frm), "text": text})
    from services.git_integration_worker.cursor_sdk_supersede import (
        live_run_for_dispatch as _live_run,
    )

    live = _live_run(dispatch_id) is not None
    eof = not live and status not in ("running", "parked_waiting", "queued")
    return {
        "lines": lines,
        "cursor": cursor,
        "eof": eof,
        "source": "agent-bus.turns",
    }


def retain_stream_prose(
    event: Any,
    *,
    dispatch_id: str,
    local: list[RunLine],
    density_harness: Any | None = None,
) -> None:
    """Keep assistant text and thinking from one stream event or fallback message."""
    if _is_stream_event(event):
        for attr in ("sdk_message", "interaction_update", "step"):
            piece = getattr(event, attr, None)
            if piece is not None:
                _retain_piece(
                    piece,
                    dispatch_id=dispatch_id,
                    local=local,
                    density_harness=density_harness,
                )
        return
    _retain_piece(
        event,
        dispatch_id=dispatch_id,
        local=local,
        density_harness=density_harness,
    )


def _is_stream_event(event: Any) -> bool:
    return any(
        getattr(event, attr, None) is not None
        for attr in ("sdk_message", "interaction_update", "step")
    )


def _retain_piece(
    piece: Any,
    *,
    dispatch_id: str,
    local: list[RunLine],
    density_harness: Any | None = None,
) -> None:
    extracted = _prose(piece)
    if extracted is None:
        return
    kind, text = extracted
    if not text or not str(text).strip():
        return
    if density_harness is not None:
        try:
            density_harness.note_prose(text)
        except Exception:  # noqa: BLE001 — meter must not break capture
            pass
    from services.git_integration_worker.cursor_sdk_supersede import (
        live_run_for_dispatch,
    )

    record = live_run_for_dispatch(dispatch_id)
    if record is not None:
        local.append(record.lines.append(kind, text))
        return
    local.append(RunLine(index=len(local) + 1, kind=kind, text=text))


def _prose(piece: Any) -> tuple[str, str] | None:
    kind = getattr(piece, "type", "")
    if kind in {"assistant", "text-delta", "assistantMessage"}:
        return "assistant", _assistant_text(piece)
    if kind in {"thinking", "thinking-delta"}:
        return "thinking", str(getattr(piece, "text", "") or "")
    if kind == "thinkingMessage":
        message = getattr(piece, "message", None)
        return "thinking", str(getattr(message, "text", "") or "")
    return None


def _assistant_text(piece: Any) -> str:
    message = getattr(piece, "message", None)
    if message is not None:
        inner = getattr(message, "text", None)
        if isinstance(inner, str):
            return inner
        content = getattr(message, "content", None) or ()
        parts: list[str] = []
        for block in content:
            if isinstance(block, Mapping):
                text = block.get("text")
            else:
                text = getattr(block, "text", None)
            if isinstance(text, str):
                parts.append(text)
        if parts:
            return "".join(parts)
    direct = getattr(piece, "text", None)
    if isinstance(direct, str):
        return direct
    return ""
