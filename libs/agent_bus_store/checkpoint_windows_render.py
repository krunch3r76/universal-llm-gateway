"""Render-on-read ``## Windows`` for role:root CHECKPOINT turns.

Joins bus CHECKPOINT turn ordinals with ``session_journals`` rows whose
``entity_ids`` cite the root thread. Read path only — never persisted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Protocol

from .checkpoint_projection import (
    _RESIDUE_HEADER,
    RESUME_FOOTER_PREFIX,
    is_checkpoint_subject,
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
_ARC_PREFIX_RE = re.compile(r"^arc:\s*", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class CheckpointTurnRow:
    turn_number: int
    cp_ordinal: int
    created_at: str
    subject: str


@dataclass(frozen=True, slots=True)
class WindowRow:
    cp_ordinal: int
    turn: int
    session_id: str | None
    arc: str | None
    journal_row_id: int | None


class JournalFetcher(Protocol):
    def __call__(self, *, thread_id: str) -> tuple[dict[str, Any], ...]: ...


class CheckpointTurnLister(Protocol):
    def __call__(self, *, thread_id: str) -> tuple[CheckpointTurnRow, ...]: ...


def extract_arc_from_summary(summary: str) -> str | None:
    """Return the ``Arc:`` one-liner from a session journal summary, if present."""
    for line in summary.splitlines():
        stripped = line.strip()
        if _ARC_PREFIX_RE.match(stripped):
            return _ARC_PREFIX_RE.sub("", stripped).strip() or None
    return None


def journal_cites_thread(*, entity_ids: list[str] | None, thread_id: str) -> bool:
    from cortex_store.dispatch_ops._session_bus_thread_disposition import (
        parse_bus_thread_refs,
    )

    return thread_id in parse_bus_thread_refs(entity_ids)


def list_checkpoint_turns(*, thread_id: str) -> tuple[CheckpointTurnRow, ...]:
    """Return CHECKPOINT turns on *thread_id* in turn_number order."""
    from .db.connection import connect

    with connect() as conn:
        rows = conn.execute(
            "SELECT turn_number, subject, created_at FROM turns "
            "WHERE thread = ? ORDER BY turn_number ASC",
            (thread_id,),
        ).fetchall()
    out: list[CheckpointTurnRow] = []
    ordinal = 0
    for row in rows:
        subject = str(row["subject"])
        if not is_checkpoint_subject(subject):
            continue
        ordinal += 1
        out.append(
            CheckpointTurnRow(
                turn_number=int(row["turn_number"]),
                cp_ordinal=ordinal,
                created_at=str(row["created_at"]),
                subject=subject,
            )
        )
    return tuple(out)


def fetch_journals_for_thread(*, thread_id: str) -> tuple[dict[str, Any], ...]:
    """Return session journal rows whose ``entity_ids`` cite *thread_id*."""
    from cortex_store.db import cortex_conn, decode_row, query

    json_fields = frozenset({"domains", "decisions", "open_items", "entity_ids"})
    with cortex_conn() as conn:
        rows = query(conn, "SELECT * FROM session_journals ORDER BY id ASC")
    matched: list[dict[str, Any]] = []
    for row in rows:
        decoded = decode_row(row, json_fields)
        if journal_cites_thread(
            entity_ids=decoded.get("entity_ids"), thread_id=thread_id
        ):
            matched.append(decoded)
    return tuple(matched)


def join_windows(
    *,
    checkpoint_turns: tuple[CheckpointTurnRow, ...],
    journals: tuple[dict[str, Any], ...],
) -> tuple[WindowRow, ...]:
    """Pair each CHECKPOINT window with the journal closed during that interval."""
    if not checkpoint_turns:
        return ()

    sorted_journals = sorted(journals, key=lambda j: str(j.get("timestamp") or ""))
    out: list[WindowRow] = []
    for idx, cp in enumerate(checkpoint_turns):
        window_start = cp.created_at
        window_end = (
            checkpoint_turns[idx + 1].created_at
            if idx + 1 < len(checkpoint_turns)
            else None
        )
        matched: list[dict[str, Any]] = []
        for journal in sorted_journals:
            ts = str(journal.get("timestamp") or "")
            if ts < window_start:
                continue
            if window_end is not None and ts >= window_end:
                continue
            matched.append(journal)
        journal = matched[-1] if matched else None
        arc = None
        journal_row_id = None
        session_id = None
        if journal is not None:
            summary = str(journal.get("summary") or "")
            arc = extract_arc_from_summary(summary)
            journal_row_id = int(journal["id"]) if journal.get("id") is not None else None
            session_id = journal.get("session_id")
            if isinstance(session_id, str):
                session_id = session_id.strip() or None
            else:
                session_id = None
        out.append(
            WindowRow(
                cp_ordinal=cp.cp_ordinal,
                turn=cp.turn_number,
                session_id=session_id,
                arc=arc,
                journal_row_id=journal_row_id,
            )
        )
    return tuple(out)


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
    fetch = journal_fetcher or fetch_journals_for_thread
    try:
        journals = fetch(thread_id=thread_id)
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
    fetch = journal_fetcher or fetch_journals_for_thread
    unrendered = False
    try:
        journals = fetch(thread_id=thread)
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
    "extract_arc_from_summary",
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
]
