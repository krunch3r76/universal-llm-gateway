"""Shared park-harvest predicates (libs-only; no services imports)."""

from __future__ import annotations

import re

from claude_bundles.conductor_stop import (
    _ARCHIVE_OR_HARVEST_RE,
    _G_ROW_RE,
    _NEXT_ADMIT_NONE_RE,
    EXIT_PERSIST_STOPS,
    is_consult_pending_wait,
    next_admit_names_harvest,
    parse_stop_tokens,
)


def mission_open(*, scoreboard_body: str) -> bool:
    """True when scoreboard fold has any G-row without DONE."""
    text = scoreboard_body or ""
    parsed = parse_stop_tokens(text)
    for match in _G_ROW_RE.finditer(text):
        gid = match.group(1)
        row_start = match.start()
        next_row = _G_ROW_RE.search(text, match.end())
        row_end = next_row.start() if next_row else len(text)
        row_text = text[row_start:row_end]
        if re.search(r"\|\s*OPEN\s*\|", row_text, re.IGNORECASE):
            return True
        row_tokens = parsed.rows.get(gid, frozenset())
        if row_tokens and "DONE" not in row_tokens:
            return True
    if not parsed.rows:
        return True
    return any("DONE" not in tokens for tokens in parsed.rows.values())


def parked_or_none_next_admit(*, body: str) -> bool:
    """True when closeout carries PARKED_TRANSPORT or explicit NEXT_ADMIT:none."""
    parsed = parse_stop_tokens(body or "")
    if "PARKED_TRANSPORT" in parsed.tokens:
        return True
    return _NEXT_ADMIT_NONE_RE.search(body or "") is not None


def harvest_still_owed(*, body: str) -> bool:
    """CONSULT_PENDING without archive, or NEXT_ADMIT names harvest (not none)."""
    text = body or ""
    if _ARCHIVE_OR_HARVEST_RE.search(text):
        return False
    if _NEXT_ADMIT_NONE_RE.search(text):
        return False
    if next_admit_names_harvest(text):
        return True
    return is_consult_pending_wait(text)


def exit_persist_terminal(*, closeout_tokens: frozenset[str]) -> bool:
    """True when terminal closeout intersects exit-persist stop tokens."""
    return bool(closeout_tokens & EXIT_PERSIST_STOPS)


def successor_owed(*, closeout_tokens: frozenset[str]) -> bool:
    """Watcher heuristic: ROW_HOP boundary would owe a hop successor."""
    if closeout_tokens & (EXIT_PERSIST_STOPS | frozenset({"DONE"})):
        return False
    return "ROW_HOP" in closeout_tokens
