"""Render-on-read ``## Windows`` for role:root CHECKPOINT turns.

Joins bus CHECKPOINT turn ordinals with ``session_journals`` rows whose
``entity_ids`` cite the root thread. Read path only — never persisted.
"""

from __future__ import annotations

import re
from itertools import groupby
from typing import Any

from .checkpoint_auto_stamp_wiring import load_thread_tags
from .checkpoint_projection import (
    _RESIDUE_HEADER,
    RESUME_FOOTER_PREFIX,
    is_checkpoint_subject,
)
from .checkpoint_windows_join import (
    CheckpointTurnLister,
    CheckpointTurnRow,
    JournalFetcher,
    WindowRow,
    compute_journal_fetch_limit,
    extract_arc_from_summary,
    fetch_journals_for_checkpoint_windows,
    fetch_journals_for_thread,
    join_windows,
    journal_cites_thread,
    list_checkpoint_turns,
    timestamp_to_utc_instant,
)
from .thread_classification import classify_thread

_WINDOWS_HEADER = "## Windows (rendered at read — do not hand-edit)"
_WINDOWS_UNRENDERED_BANNER = (
    "> **UNRENDERED** — session journals unreachable at read; "
    "windows table omitted."
)
_PRIOR_WINDOWS_RE = re.compile(
    r"^## Windows \(rendered at read — do not hand-edit\)\s*\n.*?"
    r"(?=^## |\Z)",
    re.MULTILINE | re.DOTALL,
)


def render_windows_table(rows: tuple[WindowRow, ...]) -> str:
    """Render the markdown table body (without section header)."""
    if not rows:
        return "_none_"
    lines = [
        "| cp_ordinal | turn | session_id | Arc | journal_row_id |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        session_id = row.session_id or ""
        arc = row.arc or ""
        journal_row_id = str(row.journal_row_id) if row.journal_row_id else ""
        lines.append(
            f"| {row.cp_ordinal} | {row.turn} | {session_id} | {arc} | {journal_row_id} |"
        )
    return "\n".join(lines)


def render_windows_section(
    *,
    rows: tuple[WindowRow, ...],
    unrendered: bool = False,
) -> str:
    parts = [_WINDOWS_HEADER]
    if unrendered:
        parts.append(_WINDOWS_UNRENDERED_BANNER)
    parts.append(render_windows_table(rows))
    return "\n".join(parts)


def _strip_prior_windows_render(body: str) -> str:
    return _PRIOR_WINDOWS_RE.sub("", body).rstrip()


def inject_windows_section(body: str, windows_md: str) -> str:
    """Insert read-rendered windows after derived zone, before residue/footer."""
    text = _strip_prior_windows_render(body)
    residue_idx = text.find(_RESIDUE_HEADER)
    if residue_idx >= 0:
        return f"{text[:residue_idx].rstrip()}\n\n{windows_md}\n\n{text[residue_idx:]}"
    footer_idx = text.find(RESUME_FOOTER_PREFIX)
    if footer_idx >= 0:
        return f"{text[:footer_idx].rstrip()}\n\n{windows_md}\n\n{text[footer_idx:]}"
    return f"{text.rstrip()}\n\n{windows_md}"


def should_render_windows(*, subject: str, thread_tags: list[str] | None) -> bool:
    if not is_checkpoint_subject(subject):
        return False
    classification = classify_thread(thread_tags)
    return classification["spine"] == "root"


def apply_checkpoint_windows_to_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Render CHECKPOINT windows once per thread batch for list/read hot paths."""
    if not rows:
        return rows
    if len({str(row["thread"]) for row in rows}) == 1:
        return _apply_checkpoint_windows_to_single_thread_rows(rows)
    rendered: list[dict[str, Any]] = []
    for _, group in groupby(rows, key=lambda row: str(row["thread"])):
        rendered.extend(_apply_checkpoint_windows_to_single_thread_rows(list(group)))
    return rendered


def _apply_checkpoint_windows_to_single_thread_rows(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    thread = str(rows[0]["thread"])
    thread_tags = load_thread_tags(thread)
    if classify_thread(thread_tags)["spine"] != "root":
        return rows

    checkpoint_ids = {
        int(row["id"])
        for row in rows
        if row.get("body")
        and should_render_windows(
            subject=str(row["subject"]),
            thread_tags=thread_tags,
        )
    }
    if not checkpoint_ids:
        return rows

    checkpoint_turns = list_checkpoint_turns(thread_id=thread)
    if not checkpoint_turns:
        return rows

    unrendered = False
    try:
        journals = fetch_journals_for_checkpoint_windows(
            thread_id=thread,
            checkpoint_turns=checkpoint_turns,
        )
        window_rows = join_windows(checkpoint_turns=checkpoint_turns, journals=journals)
    except Exception:
        window_rows = tuple(
            WindowRow(
                cp_ordinal=cp.cp_ordinal,
                turn=cp.turn_number,
                session_id=None,
                arc=None,
                journal_row_id=None,
            )
            for cp in checkpoint_turns
        )
        unrendered = True

    windows_md = render_windows_section(rows=window_rows, unrendered=unrendered)
    rendered: list[dict[str, Any]] = []
    for row in rows:
        if int(row["id"]) not in checkpoint_ids:
            rendered.append(row)
            continue
        updated = dict(row)
        updated["body"] = inject_windows_section(str(row["body"]), windows_md)
        rendered.append(updated)
    return rendered


def render_checkpoint_windows(
    *,
    thread_id: str,
    checkpoint_turns: tuple[CheckpointTurnRow, ...] | None = None,
    journal_fetcher: JournalFetcher | None = None,
) -> tuple[WindowRow, ...]:
    turns = (
        checkpoint_turns
        if checkpoint_turns is not None
        else list_checkpoint_turns(thread_id=thread_id)
    )
    try:
        if journal_fetcher is not None:
            journals = journal_fetcher(thread_id=thread_id)
        else:
            journals = fetch_journals_for_checkpoint_windows(
                thread_id=thread_id,
                checkpoint_turns=turns,
            )
    except Exception:
        return ()
    return join_windows(checkpoint_turns=turns, journals=journals)


def maybe_render_checkpoint_windows(
    *,
    thread: str,
    subject: str,
    body: str,
    thread_tags: list[str] | None,
    checkpoint_turns: tuple[CheckpointTurnRow, ...] | None = None,
    journal_fetcher: JournalFetcher | None = None,
) -> str:
    """Append read-rendered windows to a CHECKPOINT body when spine=root."""
    if not should_render_windows(subject=subject, thread_tags=thread_tags):
        return body
    turns = (
        checkpoint_turns
        if checkpoint_turns is not None
        else list_checkpoint_turns(thread_id=thread)
    )
    if not turns:
        return body
    unrendered = False
    try:
        if journal_fetcher is not None:
            journals = journal_fetcher(thread_id=thread)
        else:
            journals = fetch_journals_for_checkpoint_windows(
                thread_id=thread,
                checkpoint_turns=turns,
            )
        rows = join_windows(checkpoint_turns=turns, journals=journals)
    except Exception:
        rows = tuple(
            WindowRow(
                cp_ordinal=cp.cp_ordinal,
                turn=cp.turn_number,
                session_id=None,
                arc=None,
                journal_row_id=None,
            )
            for cp in turns
        )
        unrendered = True
    windows_md = render_windows_section(rows=rows, unrendered=unrendered)
    return inject_windows_section(body, windows_md)


__all__ = [
    "CheckpointTurnRow",
    "CheckpointTurnLister",
    "JournalFetcher",
    "WindowRow",
    "apply_checkpoint_windows_to_rows",
    "compute_journal_fetch_limit",
    "extract_arc_from_summary",
    "fetch_journals_for_checkpoint_windows",
    "fetch_journals_for_thread",
    "inject_windows_section",
    "join_windows",
    "journal_cites_thread",
    "list_checkpoint_turns",
    "maybe_render_checkpoint_windows",
    "render_checkpoint_windows",
    "render_windows_section",
    "render_windows_table",
    "should_render_windows",
    "timestamp_to_utc_instant",
]
