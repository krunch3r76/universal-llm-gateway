"""G-row scoreboard table geometry — cells located by header, never by position.

Scoreboard tips exist in two shapes: the hand-authored 4-column
``| ID | Deliverable | Status | Stops |`` and the 5-column
``| ID | Deliverable | Mode | Status | Stops |`` that ``render_sparse_scoreboard``
emits at birth. Every parser was first written against the 4-column shape with a
literal index, so on a sparse-born board the fold wrote the Mode cell, the tip
reader read ``plan``/``agent`` as a status and the Stops scan read the Status
cell (a:33504). Locating a cell from the header makes the two shapes one grammar
and keeps a sixth column from moving these call sites again.
"""

from __future__ import annotations

import re

SCOREBOARD_ROW_ID = r"(?:G[1-7]|R\d+)"
STATUS_HEADER = "status"
STOPS_HEADER = "stops"
# 4-column positions — what every caller assumed before the header lookup, and
# still the right answer for closeout prose that carries rows without a header.
DEFAULT_STATUS_INDEX = 3
DEFAULT_STOPS_INDEX = 4

_ROW_ID_RE = re.compile(rf"^\s*({SCOREBOARD_ROW_ID})\s*$", re.IGNORECASE)
_STATUS_TOKEN_RE = re.compile(r"[A-Za-z_()]+")


def header_indices(body: str) -> dict[str, int]:
    """Cell index per lower-cased header name, from the first header naming Status.

    Empty when the body has no such header; the Sidecars table is skipped because
    it never carries a Status column.
    """
    for line in (body or "").splitlines():
        if not line.lstrip().startswith("|"):
            continue
        names = {
            part.strip().lower(): index for index, part in enumerate(line.split("|"))
        }
        if STATUS_HEADER in names:
            return names
    return {}


def status_index(body: str) -> int:
    """Cell index of Status, falling back to the 4-column position."""
    return header_indices(body).get(STATUS_HEADER, DEFAULT_STATUS_INDEX)


def stops_index(body: str) -> int:
    """Cell index of Stops, falling back to the 4-column position."""
    return header_indices(body).get(STOPS_HEADER, DEFAULT_STOPS_INDEX)


def row_id_in(line: str) -> str | None:
    """Upper-case G/R row id when ``line`` is a scoreboard table row, else None."""
    if not line.lstrip().startswith("|"):
        return None
    parts = line.split("|")
    if len(parts) < 2:
        return None
    match = _ROW_ID_RE.match(parts[1])
    return match.group(1).upper() if match else None


def cell(line: str, index: int) -> str:
    """Stripped cell at ``index``; empty string when the row is too short."""
    parts = line.split("|")
    if index >= len(parts):
        return ""
    return parts[index].strip()


def set_cell(line: str, index: int, value: str) -> str:
    """``line`` with the cell at ``index`` replaced; unchanged when the row is too short."""
    parts = line.split("|")
    if index >= len(parts):
        return line
    parts[index] = f" {value} "
    return "|".join(parts)


def status_token(text: str) -> str | None:
    """Leading status word of a cell — reads ``WIP(conductor)`` and ``DONE — landed``."""
    match = _STATUS_TOKEN_RE.match(text.strip())
    return match.group(0).upper() if match else None


__all__ = [
    "DEFAULT_STATUS_INDEX",
    "DEFAULT_STOPS_INDEX",
    "SCOREBOARD_ROW_ID",
    "STATUS_HEADER",
    "STOPS_HEADER",
    "cell",
    "header_indices",
    "row_id_in",
    "set_cell",
    "status_index",
    "status_token",
    "stops_index",
]
