"""Assistant text and thinking kept on the live run while the stream drains.

``observe_run_stream`` used to keep tool calls, usage, and request ids and
drop prose. A reader of the registered live run can take lines after a
cursor without a spool file and without folding the text into a snapshot.
"""

from __future__ import annotations

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
            new_cursor = self._lines[-1].index if self._lines else 0
            return lines, new_cursor

    def snapshot(self) -> tuple[RunLine, ...]:
        with self._lock:
            return tuple(self._lines)


def read_run_lines(
    dispatch_id: str, cursor: int = 0
) -> tuple[tuple[RunLine, ...], int]:
    """Prose on the live run for ``dispatch_id`` after ``cursor``.

    An unregistered dispatch returns no lines and leaves the cursor unchanged.
    """
    from services.git_integration_worker.cursor_sdk_supersede import (
        live_run_for_dispatch,
    )

    record = live_run_for_dispatch(dispatch_id)
    if record is None:
        return (), cursor
    return record.lines.after(cursor)


def retain_stream_prose(
    event: Any,
    *,
    dispatch_id: str,
    local: list[RunLine],
) -> None:
    """Keep assistant text and thinking from one stream event or fallback message."""
    if _is_stream_event(event):
        for attr in ("sdk_message", "interaction_update", "step"):
            piece = getattr(event, attr, None)
            if piece is not None:
                _retain_piece(piece, dispatch_id=dispatch_id, local=local)
        return
    _retain_piece(event, dispatch_id=dispatch_id, local=local)


def _is_stream_event(event: Any) -> bool:
    return any(
        getattr(event, attr, None) is not None
        for attr in ("sdk_message", "interaction_update", "step")
    )


def _retain_piece(piece: Any, *, dispatch_id: str, local: list[RunLine]) -> None:
    extracted = _prose(piece)
    if extracted is None:
        return
    kind, text = extracted
    if not text or not str(text).strip():
        return
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
