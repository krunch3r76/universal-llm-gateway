"""Forward-only conductor scoreboard journal — cortex sidecar + tip.

Authority for scoreboard tip writes and append-only mutation records keyed by
todo slug. Conductor sessions mutate via ``forward_mutate_tip`` only.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from implement_admission.conductor_score_io import (
    JournalAppendResult,
    JournalRecord,
    append_journal_record_at,
    birth_scoreboard_at,
    forward_mutate_tip_at,
    load_journal_at,
    recover_tip_from_journal_at,
    tip_sha256,
)
from implement_admission.conductor_score_locus import ScoreboardLocus, work_item_locus
from implement_admission.conductor_score_table import SCOREBOARD_ROW_ID

if TYPE_CHECKING:
    from implement_admission.conductor_witness import FoldDeps

_CLOSED_ROW_RE = re.compile(
    rf"^\|\s*({SCOREBOARD_ROW_ID})\s*\|[^|]*\|(?:[^|]*\|)?\s*DONE\b",
    re.IGNORECASE | re.MULTILINE,
)
_ROW_STATUS_RE = re.compile(
    rf"^\|\s*({SCOREBOARD_ROW_ID})\s*\|[^|]*\|(?:[^|]*\|)?\s*(?P<status>[A-Za-z_()]+)",
    re.MULTILINE,
)
STATUS_VOCABULARY: frozenset[str] = frozenset(
    {"OPEN", "DONE", "CLAIMED", "WIP", "RETRACTED"}
)
G_ROWS: tuple[str, ...] = ("G1", "G2", "G3", "G4", "G5", "G6", "G7")
_G_LABELS: dict[str, str] = {
    "G1": "Architecture / recon",
    "G2": "Frame",
    "G3": "Densify",
    "G4": "Skeptic / gate-6",
    "G5": "Implement",
    "G6": "Pre-land review",
    "G7": "Ship / land",
}
_G6_PRE_LAND_REVIEW = (
    "G6 pre-land review witness — `cdp/opus-5` `purpose=review` on the lane "
    "branch diff before merge; harvest ≺ land (a:32226 · a:32146)."
)
_WITNESS_KIND_BIND = "BIND"
_WITNESS_KIND_LAND = "LAND"


def is_g_ladder_rows(rows: tuple[str, ...]) -> bool:
    """True when rows are the default seven-row G-ladder."""
    return rows == G_ROWS


def default_row_mode(row_id: str) -> str:
    """Default Mode column for a G-row at sparse scoreboard birth (D3)."""
    if row_id == "G3":
        return "plan"
    if row_id == "G5":
        return "agent"
    return "—"


def resolve_scoreboard_rows(attrs: dict[str, Any]) -> tuple[str, ...]:
    """Return scoreboard row ids from attrs.rows or acceptance_criteria fallback."""
    raw_rows = attrs.get("rows")
    if isinstance(raw_rows, list) and raw_rows:
        cleaned = tuple(str(row).strip() for row in raw_rows if str(row).strip())
        if cleaned:
            return cleaned
    derived = attrs.get("derived_from")
    acceptance = attrs.get("acceptance_criteria")
    if derived and isinstance(acceptance, list) and acceptance:
        labels = [str(item).strip() for item in acceptance if str(item).strip()]
        if labels:
            return tuple(f"R{i}" for i in range(1, len(labels) + 1))
    return G_ROWS


def resolve_row_labels(
    rows: tuple[str, ...],
    attrs: dict[str, Any],
) -> dict[str, str]:
    """Map row ids to deliverable labels for scoreboard rendering."""
    if is_g_ladder_rows(rows):
        return dict(_G_LABELS)
    raw_rows = attrs.get("rows")
    if isinstance(raw_rows, list) and len(raw_rows) == len(rows):
        labels = {
            str(row_id).strip(): str(label).strip()
            for row_id, label in zip(rows, raw_rows, strict=False)
            if str(row_id).strip() and str(label).strip()
        }
        if len(labels) == len(rows):
            return labels
    acceptance = attrs.get("acceptance_criteria")
    if isinstance(acceptance, list) and len(acceptance) == len(rows):
        return {
            row_id: str(label).strip()
            for row_id, label in zip(rows, acceptance, strict=False)
            if str(label).strip()
        }
    return {row_id: row_id for row_id in rows}


def _witness_sidecar_lines(
    rows: tuple[str, ...],
    row_labels: dict[str, str],
) -> list[str]:
    """Seed sidecar placeholder rows keyed by witness kind."""
    if is_g_ladder_rows(rows):
        return [
            "| F1 | (pending) | G2 frame witness slot |",
            "| S7 | (pending) | G2 frame witness slot |",
            "| S4b | (pending) | G3 spec witness slot |",
            "| S9 | (pending) | G3 spec witness slot |",
            "| G4 | (pending) | G4 skeptic verdict slot |",
            f"| R1 | (pending) | {_G6_PRE_LAND_REVIEW} |",
            "| L1 | (pending) | G7 land sha slot |",
        ]
    lines: list[str] = []
    for row_id in rows:
        label = row_labels.get(row_id, row_id)
        lines.append(
            f"| {row_id}-{_WITNESS_KIND_BIND} | (pending) | {label} bind witness slot |"
        )
        lines.append(
            f"| {row_id}-{_WITNESS_KIND_LAND} | (pending) | {label} land sha slot |"
        )
    return lines


def render_sparse_scoreboard(
    *,
    source_ref: str,
    slug: str,
    entry_gate: str,
    stop_after: str | None,
    rows: tuple[str, ...] = G_ROWS,
    row_labels: dict[str, str] | None = None,
) -> str:
    """Build the forward-only sparse scoreboard tip for a todo conductor session."""
    tip_uri = scoreboard_tip_uri(slug)
    journal_uri = scoreboard_journal_uri(slug)
    stop_after_line = f"- **stop_after:** {stop_after}" if stop_after else ""
    labels = row_labels or (
        dict(_G_LABELS) if is_g_ladder_rows(rows) else {row_id: row_id for row_id in rows}
    )
    table_rows = "\n".join(
        f"| {row_id} | {labels.get(row_id, row_id)} | {default_row_mode(row_id)} | OPEN | |"
        for row_id in rows
    )
    parts = [
        f"# Scoreboard — {source_ref}",
        "",
        f"- **Work item:** `{source_ref}`",
        f"- **Scoreboard URI:** `{tip_uri}`",
        f"- **Journal URI:** `{journal_uri}`",
        f"- **Entry gate:** {entry_gate}",
    ]
    if stop_after_line:
        parts.append(stop_after_line)
    parts.extend(
        [
            "",
            "## Gated deliverables",
            "",
            "| ID | Deliverable | Mode | Status | Stops |",
            "|---|---|---|---|",
            table_rows,
            "",
            "## Sidecars",
            "",
            "| ID | Artifact URI | What it is |",
            "|---|---|---|",
            *_witness_sidecar_lines(rows, labels),
            "",
        ]
    )
    return "\n".join(parts)


def scoreboard_tip_uri(slug: str) -> str:
    """Return cortex:// URI for a work-item scoreboard tip."""
    return work_item_locus(slug).tip_uri


def scoreboard_journal_uri(slug: str) -> str:
    """Return cortex:// URI for a work-item scoreboard journal sidecar."""
    return work_item_locus(slug).journal_uri


def _work_item(slug: str, files_root: Path | None) -> ScoreboardLocus:
    return work_item_locus(
        slug, files_root=files_root, rewind_guard=reject_rewind_closed_row
    )


def _tip_path(slug: str, *, files_root: Path | None = None) -> Path:
    return work_item_locus(slug, files_root=files_root).tip_path


def _journal_path(slug: str, *, files_root: Path | None = None) -> Path:
    return work_item_locus(slug, files_root=files_root).journal_path


def recover_tip_from_journal(slug: str, *, files_root: Path | None = None) -> bool:
    """Restore a work-item tip from its journal when the disk tip is missing or stale."""
    return recover_tip_from_journal_at(_work_item(slug, files_root))


def read_tip(
    slug: str,
    *,
    files_root: Path | None = None,
    fold_deps: FoldDeps | None = None,
) -> tuple[str, str] | None:
    """Return ``(body, sha256)`` for a work-item tip, healing from journal when needed."""
    recover_tip_from_journal(slug, files_root=files_root)
    path = _tip_path(slug, files_root=files_root)
    if not path.is_file():
        return None
    body = path.read_text(encoding="utf-8")
    if fold_deps is not None:
        from implement_admission.conductor_witness import fold_scoreboard

        fold = fold_scoreboard(slug, deps=fold_deps, files_root=files_root)
        if fold is not None:
            return fold.folded_body, fold.tip_sha or tip_sha256(fold.folded_body)
    return body, tip_sha256(body)


def load_journal(slug: str, *, files_root: Path | None = None) -> list[dict[str, Any]]:
    """Load append-only journal records for a work-item slug."""
    return load_journal_at(_work_item(slug, files_root))


def closed_rows_in_tip(
    body: str,
    *,
    witnessed_done: frozenset[str] | None = None,
) -> frozenset[str]:
    """Return G-row ids marked DONE in the scoreboard tip (witnessed when supplied)."""
    if witnessed_done is not None:
        return witnessed_done
    return frozenset(_CLOSED_ROW_RE.findall(body))


def _row_status(body: str, gid: str) -> str | None:
    for match in _ROW_STATUS_RE.finditer(body):
        if match.group(1).upper() == gid.upper():
            return match.group("status").strip().upper()
    return None


def reject_rewind_closed_row(
    *,
    prior_body: str,
    next_body: str,
    prior_witnessed_done: frozenset[str] | None = None,
) -> str | None:
    """Return rejection reason when next_body rewinds a witnessed-closed G-row."""
    prior_closed = closed_rows_in_tip(prior_body, witnessed_done=prior_witnessed_done)
    if not prior_closed:
        return None
    for gid in prior_closed:
        next_status = _row_status(next_body, gid)
        if next_status != "DONE":
            return f"rewind closed row: {gid}"
    return None


def append_journal_record(
    slug: str,
    record: JournalRecord,
    *,
    files_root: Path | None = None,
) -> None:
    """Append one mutation record to a work-item journal sidecar."""
    append_journal_record_at(_work_item(slug, files_root), record)


def birth_scoreboard(
    slug: str,
    *,
    scoreboard_body: str,
    seat: str = "materializer",
    dispatch_id: str | None = None,
    reason: str = "conductor spawn birth",
    rows: tuple[str, ...] = G_ROWS,
    delta: str = "sparse birth",
    files_root: Path | None = None,
) -> str:
    """Append birth journal record then write sparse tip; return tip sha256."""
    return birth_scoreboard_at(
        _work_item(slug, files_root),
        scoreboard_body=scoreboard_body,
        seat=seat,
        dispatch_id=dispatch_id,
        reason=reason,
        rows=rows,
        delta=delta,
    )


def forward_mutate_tip(
    slug: str,
    *,
    next_body: str,
    seat: str,
    dispatch_id: str | None,
    reason: str,
    rows: tuple[str, ...],
    delta: str,
    files_root: Path | None = None,
    prior_witnessed_done: frozenset[str] | None = None,
) -> JournalAppendResult:
    """Forward-only journal-then-tip mutation for a work-item slug."""
    return forward_mutate_tip_at(
        _work_item(slug, files_root),
        next_body=next_body,
        seat=seat,
        dispatch_id=dispatch_id,
        reason=reason,
        rows=rows,
        delta=delta,
        prior_witnessed_done=prior_witnessed_done,
    )


def walk_journal_to_tip(
    slug: str,
    *,
    files_root: Path | None = None,
) -> str | None:
    """Return the tip sha reached by walking journal records, or None if empty."""
    records = load_journal(slug, files_root=files_root)
    if not records:
        tip = read_tip(slug, files_root=files_root)
        return tip[1] if tip else None
    last = records[-1]
    sha = last.get("tip_sha")
    return str(sha) if sha else None
