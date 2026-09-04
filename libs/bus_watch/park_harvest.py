"""Shared park-harvest predicates (libs-only; no services imports)."""

from __future__ import annotations

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
    """True when scoreboard fold has any G-row whose Status cell is not DONE."""
    from implement_admission.conductor_witness_types import row_status_in_tip

    text = scoreboard_body or ""
    g_ids = [match.group(1) for match in _G_ROW_RE.finditer(text)]
    if not g_ids:
        return True
    return any((row_status_in_tip(text, gid) or "OPEN").upper() != "DONE" for gid in g_ids)


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
