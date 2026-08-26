"""Forward-only conductor scoreboard journal — cortex sidecar + tip.

Authority for scoreboard tip writes and append-only mutation records keyed by
todo slug. Conductor sessions mutate via ``forward_mutate_tip`` only.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from durable_io.atomic import durable_write_text

from implement_admission.closeout_helpers import cortex_files_root

if TYPE_CHECKING:
    from implement_admission.conductor_witness import FoldDeps

_SCOREBOARDS_DIR = "notes/system/scoreboards"
_WRITER_ID = "implement_admission.conductor_score_journal"
_RECORD_SEP = "\n---\n"
_CLOSED_ROW_RE = re.compile(
    r"^\|\s*(G[1-6])\s*\|[^|]*\|\s*DONE\b",
    re.IGNORECASE | re.MULTILINE,
)
_ROW_STATUS_RE = re.compile(
    r"^\|\s*(G[1-6])\s*\|[^|]*\|\s*(?P<status>[A-Za-z_()]+)",
    re.MULTILINE,
)
STATUS_VOCABULARY: frozenset[str] = frozenset(
    {"OPEN", "DONE", "CLAIMED", "WIP", "RETRACTED"}
)
G_ROWS: tuple[str, ...] = ("G1", "G2", "G3", "G4", "G5", "G6")
_G_LABELS: dict[str, str] = {
    "G1": "Architecture / recon",
    "G2": "Frame",
    "G3": "Densify",
    "G4": "Skeptic / gate-6",
    "G5": "Implement",
    "G6": "Ship / land",
}
_AFTER_SHIP_OVERLAY = (
    "After-ship `cdp/opus-5` `purpose=review` of the landed diff — good default; "
    "¬ a gated G-row (done-claim must not wait on it)."
)


def render_sparse_scoreboard(
    *,
    source_ref: str,
    slug: str,
    entry_gate: str,
    stop_after: str | None,
) -> str:
    """Build the forward-only sparse scoreboard tip for a todo conductor session."""
    tip_uri = scoreboard_tip_uri(slug)
    journal_uri = scoreboard_journal_uri(slug)
    stop_after_line = f"- **stop_after:** {stop_after}" if stop_after else ""
    rows = "\n".join(f"| {gid} | {_G_LABELS[gid]} | OPEN | |" for gid in G_ROWS)
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
            "| ID | Deliverable | Status | Stops |",
            "|---|---|---|---|",
            rows,
            "",
            "## Sidecars",
            "",
            "| ID | Artifact URI | What it is |",
            "|---|---|---|",
            f"| S1 | (overlay) | {_AFTER_SHIP_OVERLAY} |",
            "",
        ]
    )
    return "\n".join(parts)


@dataclass(frozen=True, slots=True)
class JournalRecord:
    """One append-only score mutation record with recoverable tip body."""

    prior_tip_sha: str | None
    tip_sha: str
    tip_body: str
    seat: str
    dispatch_id: str | None
    reason: str
    rows: tuple[str, ...]
    delta: str
    written_at: str


@dataclass(frozen=True, slots=True)
class JournalAppendResult:
    """Outcome of a forward journal append + tip write."""

    tip_sha: str
    record_count: int
    rejected_reason: str | None = None


def scoreboard_tip_uri(slug: str) -> str:
    """Return cortex:// URI for the mutable scoreboard tip."""
    return f"cortex://{_SCOREBOARDS_DIR}/{slug}-scoreboard.md"


def scoreboard_journal_uri(slug: str) -> str:
    """Return cortex:// URI for the append-only journal sidecar."""
    return f"cortex://{_SCOREBOARDS_DIR}/{slug}-score-journal.md"


def _tip_path(slug: str, *, files_root: Path | None = None) -> Path:
    root = files_root if files_root is not None else cortex_files_root()
    return root / _SCOREBOARDS_DIR / f"{slug}-scoreboard.md"


def _journal_path(slug: str, *, files_root: Path | None = None) -> Path:
    root = files_root if files_root is not None else cortex_files_root()
    return root / _SCOREBOARDS_DIR / f"{slug}-score-journal.md"


def tip_sha256(body: str) -> str:
    """Hash the scoreboard tip body for provenance chaining."""
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _valid_tip_sha(sha: object) -> bool:
    if not isinstance(sha, str):
        return False
    return bool(re.fullmatch(r"[0-9a-f]{64}", sha.strip().lower()))


def recover_tip_from_journal(slug: str, *, files_root: Path | None = None) -> bool:
    """Restore tip from last journal ``tip_body`` when disk tip is missing or stale."""
    records = load_journal(slug, files_root=files_root)
    if not records:
        return False
    last = records[-1]
    expected_sha = last.get("tip_sha")
    tip_body = last.get("tip_body")
    if not _valid_tip_sha(expected_sha) or not isinstance(tip_body, str):
        return False
    path = _tip_path(slug, files_root=files_root)
    disk_sha: str | None = None
    if path.is_file():
        disk_sha = tip_sha256(path.read_text(encoding="utf-8"))
    if disk_sha == str(expected_sha):
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    durable_write_text(path, tip_body)
    return True


def read_tip(
    slug: str,
    *,
    files_root: Path | None = None,
    fold_deps: FoldDeps | None = None,
) -> tuple[str, str] | None:
    """Return ``(body, sha256)`` for the current tip, healing from journal when needed."""
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


def _parse_journal_chunk(chunk: str) -> list[dict[str, Any]]:
    try:
        parsed = json.loads(chunk)
    except json.JSONDecodeError:
        parsed = None
    else:
        if isinstance(parsed, dict):
            return [parsed]
    records: list[dict[str, Any]] = []
    for line in chunk.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            records.append(row)
    return records


def _parse_journal(text: str) -> list[dict[str, Any]]:
    if not text.strip():
        return []
    chunks = [part.strip() for part in text.split(_RECORD_SEP) if part.strip()]
    if not chunks:
        return []
    records: list[dict[str, Any]] = []
    for chunk in chunks:
        records.extend(_parse_journal_chunk(chunk))
    return records


def load_journal(slug: str, *, files_root: Path | None = None) -> list[dict[str, Any]]:
    """Load append-only journal records for a todo slug."""
    path = _journal_path(slug, files_root=files_root)
    if not path.is_file():
        return []
    return _parse_journal(path.read_text(encoding="utf-8"))


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


def _record_to_json(record: JournalRecord) -> str:
    payload = {
        "prior_tip_sha": record.prior_tip_sha,
        "tip_sha": record.tip_sha,
        "tip_body": record.tip_body,
        "seat": record.seat,
        "dispatch_id": record.dispatch_id,
        "reason": record.reason,
        "rows": list(record.rows),
        "delta": record.delta,
        "written_at": record.written_at,
        "written_by": _WRITER_ID,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def append_journal_record(
    slug: str,
    record: JournalRecord,
    *,
    files_root: Path | None = None,
) -> None:
    """Append one mutation record to the journal sidecar."""
    path = _journal_path(slug, files_root=files_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    block = _record_to_json(record)
    if path.is_file():
        existing = path.read_text(encoding="utf-8")
        sep = _RECORD_SEP if existing.strip() else ""
        durable_write_text(path, existing + sep + block + "\n")
    else:
        durable_write_text(path, block + "\n")


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
    new_sha = tip_sha256(scoreboard_body)
    append_journal_record(
        slug,
        JournalRecord(
            prior_tip_sha=None,
            tip_sha=new_sha,
            tip_body=scoreboard_body,
            seat=seat,
            dispatch_id=dispatch_id,
            reason=reason,
            rows=rows,
            delta=delta,
            written_at=datetime.now(UTC).isoformat(),
        ),
        files_root=files_root,
    )
    path = _tip_path(slug, files_root=files_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    durable_write_text(path, scoreboard_body)
    return new_sha


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
    """Forward-only journal-then-tip mutation; reject witnessed-closed-row rewind."""
    recover_tip_from_journal(slug, files_root=files_root)
    prior = read_tip(slug, files_root=files_root)
    prior_sha = prior[1] if prior else None
    if prior is not None:
        reject = reject_rewind_closed_row(
            prior_body=prior[0],
            next_body=next_body,
            prior_witnessed_done=prior_witnessed_done,
        )
        if reject:
            return JournalAppendResult(
                tip_sha=prior_sha or "",
                record_count=len(load_journal(slug, files_root=files_root)),
                rejected_reason=reject,
            )
    new_sha = tip_sha256(next_body)
    append_journal_record(
        slug,
        JournalRecord(
            prior_tip_sha=prior_sha,
            tip_sha=new_sha,
            tip_body=next_body,
            seat=seat,
            dispatch_id=dispatch_id,
            reason=reason,
            rows=rows,
            delta=delta,
            written_at=datetime.now(UTC).isoformat(),
        ),
        files_root=files_root,
    )
    path = _tip_path(slug, files_root=files_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    durable_write_text(path, next_body)
    return JournalAppendResult(
        tip_sha=new_sha,
        record_count=len(load_journal(slug, files_root=files_root)),
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
