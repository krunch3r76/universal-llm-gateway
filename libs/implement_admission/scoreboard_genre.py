"""Scoreboard document genre — detected from row-table shape, not locus family.

Charter and conductor tips share the same G-ladder and R-ledger grammars when
their tables carry ``| Gn |`` or ``| Rn |`` rows. Genre selection keys off that
shape so a charter URI does not skip projection merely because it lives under
``notes/system/threads/``.
"""

from __future__ import annotations

import re
from typing import Literal

from implement_admission.conductor_score_table import (
    cell,
    row_id_in,
    status_index,
    status_token,
)

ScoreboardGenre = Literal["g_ladder", "r_ledger", "none"]

_G_ROW_RE = re.compile(r"^G[1-7]$", re.IGNORECASE)
_R_ROW_RE = re.compile(r"^R\d+$", re.IGNORECASE)
_BOLD_STRIP_RE = re.compile(r"^\*+|\*+$")


def _status_from_cell(text: str) -> str | None:
    """Status token from a cell, tolerating charter ``**DONE**`` bold markers."""
    cleaned = _BOLD_STRIP_RE.sub("", text.strip())
    return status_token(cleaned)


def detect_genre(body: str) -> ScoreboardGenre:
    """Classify a scoreboard tip by its row-table shape."""
    has_g = False
    has_r = False
    for line in (body or "").splitlines():
        row_id = row_id_in(line)
        if not row_id:
            continue
        if _G_ROW_RE.match(row_id):
            has_g = True
        elif _R_ROW_RE.match(row_id):
            has_r = True
    if has_g:
        return "g_ladder"
    if has_r:
        return "r_ledger"
    return "none"


def ordered_row_ids(body: str) -> tuple[str, ...]:
    """Row ids in first-seen document order."""
    seen: list[str] = []
    for line in (body or "").splitlines():
        row_id = row_id_in(line)
        if row_id and row_id not in seen:
            seen.append(row_id)
    return tuple(seen)


def project_row_status(body: str) -> dict[str, str]:
    """Read-only status map from the tip — no witness rewrite, no journal write."""
    column = status_index(body)
    row_status: dict[str, str] = {}
    for line in (body or "").splitlines():
        row_id = row_id_in(line)
        if not row_id:
            continue
        token = _status_from_cell(cell(line, column))
        if token:
            row_status[row_id] = token
    return row_status


def fold_row_lines(body: str) -> list[str]:
    """Table lines for every G/R row in the tip."""
    lines: list[str] = []
    for line in (body or "").splitlines():
        if line.lstrip().startswith("|") and row_id_in(line):
            lines.append(line.strip())
    return lines


__all__ = [
    "ScoreboardGenre",
    "detect_genre",
    "fold_row_lines",
    "ordered_row_ids",
    "project_row_status",
]
