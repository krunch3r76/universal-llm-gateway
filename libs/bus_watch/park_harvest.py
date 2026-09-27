"""Shared park-harvest predicates (libs-only; no services imports)."""

from __future__ import annotations

from claude_bundles.conductor_stop import (
    _ARCHIVE_OR_HARVEST_RE,
    _G_ROW_RE,
    _NEXT_ADMIT_NONE_RE,
    EXIT_PERSIST_STOPS,
    consult_pending_blocks_progression,
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
    return any(
        (row_status_in_tip(text, gid) or "OPEN").upper() != "DONE" for gid in g_ids
    )


def work_item_slug(work_key: str) -> str:
    """Todo slug from ``work_key``. Empty when the key is not a safe slug."""
    text = str(work_key or "").strip()
    if text.lower().startswith("todo:"):
        text = text.split(":", 1)[1].strip()
    if not text or "/" in text or "\\" in text or ".." in text:
        return ""
    return text


def load_work_item_scoreboard(work_key: str) -> str | None:
    """Work-item scoreboard tip. None when the tip file is absent."""
    slug = work_item_slug(work_key)
    if not slug:
        return None
    from implement_admission.conductor_score_locus import work_item_locus

    path = work_item_locus(slug).tip_path
    if not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def g_rows_all_done(*, scoreboard_body: str) -> bool:
    """True when the tip has G-rows and every status cell is DONE.

    A tip with no G-rows is not complete. ``mission_open`` treats that as open.
    """
    if not str(scoreboard_body or "").strip():
        return False
    return not mission_open(scoreboard_body=scoreboard_body)


def scoreboard_g_rows_done(work_key: str) -> bool:
    """True when every G-row on that work_key's scoreboard tip is DONE."""
    body = load_work_item_scoreboard(work_key)
    if body is None:
        return False
    return g_rows_all_done(scoreboard_body=body)


def parked_or_none_next_admit(*, body: str) -> bool:
    """True when closeout carries PARKED_TRANSPORT or explicit NEXT_ADMIT:none."""
    parsed = parse_stop_tokens(body or "")
    if "PARKED_TRANSPORT" in parsed.tokens:
        return True
    return _NEXT_ADMIT_NONE_RE.search(body or "") is not None


def harvest_still_owed(*, body: str) -> bool:
    """PARKED_TRANSPORT, CONSULT_PENDING wait, or NEXT_ADMIT harvest — not none/archive."""
    text = body or ""
    if _ARCHIVE_OR_HARVEST_RE.search(text):
        return False
    if _NEXT_ADMIT_NONE_RE.search(text):
        return False
    if next_admit_names_harvest(text):
        return True
    if "PARKED_TRANSPORT" in parse_stop_tokens(text).tokens:
        return True
    return is_consult_pending_wait(text)


def exit_persist_terminal(*, closeout_tokens: frozenset[str]) -> bool:
    """True when terminal closeout intersects exit-persist stop tokens."""
    return bool(closeout_tokens & EXIT_PERSIST_STOPS)


def successor_owed(
    *,
    closeout_tokens: frozenset[str],
    closeout_body: str = "",
) -> bool:
    """Watcher heuristic: ROW_HOP boundary would owe a hop successor."""
    if closeout_tokens & (EXIT_PERSIST_STOPS | frozenset({"DONE"})):
        return False
    if consult_pending_blocks_progression(closeout_body):
        return False
    return "ROW_HOP" in closeout_tokens
