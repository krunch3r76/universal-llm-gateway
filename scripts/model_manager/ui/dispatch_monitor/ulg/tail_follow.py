"""Poll TailPort for the published selection and emit new lines."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol


class _TailPort(Protocol):
    def tail(self, kind: str, key: str, cursor: int) -> Mapping[str, Any]:
        """Return lines after ``cursor``."""
        ...


@dataclass
class FollowState:
    """Cursor held by the pane, not by the board."""

    kind: str | None = None
    key: str | None = None
    cursor: int = 0
    eof_announced: bool = False


def follow_step(
    state: FollowState,
    selection: Mapping[str, str] | None,
    port: _TailPort,
) -> list[str]:
    """Lines to print for one poll. Updates ``state`` in place."""
    if not selection:
        return []
    kind = selection.get("kind") or ""
    key = selection.get("key") or ""
    if not kind or not key:
        return []
    out: list[str] = []
    if state.kind != kind or state.key != key:
        state.kind = kind
        state.key = key
        state.cursor = 0
        state.eof_announced = False
        label = selection.get("label") or key
        out.append(f"--- {kind} {label} ---")
    body = port.tail(kind, key, state.cursor)
    new_cursor = body.get("cursor")
    if isinstance(new_cursor, int):
        state.cursor = new_cursor
    lines = body.get("lines")
    if isinstance(lines, list):
        for line in lines:
            if not isinstance(line, Mapping):
                continue
            text = line.get("text")
            if not isinstance(text, str) or not text:
                continue
            line_kind = line.get("kind")
            shown = line_kind if isinstance(line_kind, str) and line_kind else "text"
            out.append(f"[{shown}] {text}")
    if body.get("eof") and not state.eof_announced:
        state.eof_announced = True
        error = body.get("error")
        if isinstance(error, str) and error:
            out.append(f"(eof {error})")
        else:
            out.append("(eof)")
    if not body.get("eof"):
        state.eof_announced = False
    return out
