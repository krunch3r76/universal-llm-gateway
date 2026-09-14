"""Locus-addressed journal I/O — read, recover, append, mutate a tip."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from durable_io.atomic import durable_write_text

from implement_admission.conductor_score_locus import ScoreboardLocus
from implement_admission.events_conductor_score import emit_conductor_score_tip_mutated

_WRITER_ID = "implement_admission.conductor_score_journal"
_RECORD_SEP = "\n---\n"


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


def tip_sha256(body: str) -> str:
    """Hash the scoreboard tip body for provenance chaining."""
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _valid_tip_sha(sha: object) -> bool:
    if not isinstance(sha, str):
        return False
    return bool(re.fullmatch(r"[0-9a-f]{64}", sha.strip().lower()))


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


def load_journal_at(locus: ScoreboardLocus) -> list[dict[str, Any]]:
    """Load append-only journal records for a locus."""
    path = locus.journal_path
    if not path.is_file():
        return []
    return _parse_journal(path.read_text(encoding="utf-8"))


def recover_tip_from_journal_at(locus: ScoreboardLocus) -> bool:
    """Restore tip from last journal ``tip_body`` when disk tip is missing or stale."""
    records = load_journal_at(locus)
    if not records:
        return False
    last = records[-1]
    expected_sha = last.get("tip_sha")
    tip_body = last.get("tip_body")
    if not _valid_tip_sha(expected_sha) or not isinstance(tip_body, str):
        return False
    path = locus.tip_path
    disk_sha: str | None = None
    if path.is_file():
        disk_sha = tip_sha256(path.read_text(encoding="utf-8"))
    if disk_sha == str(expected_sha):
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    durable_write_text(path, tip_body)
    return True


def read_tip_at(locus: ScoreboardLocus) -> tuple[str, str] | None:
    """Return ``(body, sha256)`` for the current tip, healing from journal when needed."""
    recover_tip_from_journal_at(locus)
    path = locus.tip_path
    if not path.is_file():
        return None
    body = path.read_text(encoding="utf-8")
    return body, tip_sha256(body)


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


def append_journal_record_at(locus: ScoreboardLocus, record: JournalRecord) -> None:
    """Append one mutation record to the journal sidecar at ``locus``."""
    path = locus.journal_path
    path.parent.mkdir(parents=True, exist_ok=True)
    block = _record_to_json(record)
    if path.is_file():
        existing = path.read_text(encoding="utf-8")
        sep = _RECORD_SEP if existing.strip() else ""
        durable_write_text(path, existing + sep + block + "\n")
    else:
        durable_write_text(path, block + "\n")


def birth_scoreboard_at(
    locus: ScoreboardLocus,
    *,
    scoreboard_body: str,
    seat: str = "materializer",
    dispatch_id: str | None = None,
    reason: str = "conductor spawn birth",
    rows: tuple[str, ...] = (),
    delta: str = "sparse birth",
) -> str:
    """Append birth journal record then write tip at ``locus``; return tip sha256."""
    new_sha = tip_sha256(scoreboard_body)
    append_journal_record_at(
        locus,
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
    )
    locus.tip_path.parent.mkdir(parents=True, exist_ok=True)
    durable_write_text(locus.tip_path, scoreboard_body)
    emit_conductor_score_tip_mutated(
        tip_uri=locus.tip_uri,
        prior_tip_sha=None,
        tip_sha=new_sha,
        reason=reason,
    )
    return new_sha


def forward_mutate_tip_at(
    locus: ScoreboardLocus,
    *,
    next_body: str,
    seat: str,
    dispatch_id: str | None,
    reason: str,
    rows: tuple[str, ...],
    delta: str,
    prior_witnessed_done: frozenset[str] | None = None,
) -> JournalAppendResult:
    """Forward-only journal-then-tip mutation at ``locus``.

    The rewind guard lives on the locus: work-item loci reject DONE→OPEN;
    charter loci carry no guard (charter status is seat-authored free text).
    """
    recover_tip_from_journal_at(locus)
    prior = read_tip_at(locus)
    prior_sha = prior[1] if prior else None
    if prior is not None and locus.rewind_guard is not None:
        reject = locus.rewind_guard(
            prior_body=prior[0],
            next_body=next_body,
            prior_witnessed_done=prior_witnessed_done,
        )
        if reject:
            return JournalAppendResult(
                tip_sha=prior_sha or "",
                record_count=len(load_journal_at(locus)),
                rejected_reason=reject,
            )
    new_sha = tip_sha256(next_body)
    append_journal_record_at(
        locus,
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
    )
    locus.tip_path.parent.mkdir(parents=True, exist_ok=True)
    durable_write_text(locus.tip_path, next_body)
    emit_conductor_score_tip_mutated(
        tip_uri=locus.tip_uri,
        prior_tip_sha=prior_sha,
        tip_sha=new_sha,
        reason=reason,
    )
    return JournalAppendResult(
        tip_sha=new_sha,
        record_count=len(load_journal_at(locus)),
    )
