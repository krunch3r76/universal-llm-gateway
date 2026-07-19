"""Digest ledger CRUD — idempotence watermark for journal-digest entries."""

from __future__ import annotations

import hashlib
import re
import sqlite3
from typing import Any

from .db import json_decode, json_encode

_JSON_FIELDS = frozenset(
    {
        "emitted_ids",
        "verify_verdicts",
        "superseded_ids",
        "retracted_ids",
        "carried_forward_ids",
    }
)
_EFFECTIVE_STATUSES = ("staged", "committed")

_ISO_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})")
_ISO_DATE_IN_TEXT_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_MONTH_DATE_RE = re.compile(
    r"\b("
    r"January|February|March|April|May|June|July|August|September|"
    r"October|November|December"
    r")\s+(\d{1,2}),?\s+(\d{4})\b",
    re.IGNORECASE,
)
_MONTH_TO_NUM = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}
_VALID_FROM_FIELDS = (
    "valid_from",
    "valid_from_hint",
    "date_hint",
    "effective_date",
    "date",
)


def compute_entry_content_sha256(text: str) -> str:
    """Return ``sha256:<hex>`` of the UTF-8 bytes of *text*."""
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _decode_row(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    for field in _JSON_FIELDS:
        if field in out:
            fallback: Any = [] if field == "emitted_ids" else {}
            out[field] = json_decode(out.get(field), fallback=fallback)
    return out


def lookup(
    conn: sqlite3.Connection,
    journal_entity_id: str,
    entry_anchor: str,
    content_sha256: str,
) -> dict[str, Any] | None:
    """Return the ledger row for an exact (journal, anchor, sha) triple."""
    row = conn.execute(
        "SELECT * FROM digest_ledger "
        "WHERE journal_entity_id = ? AND entry_anchor = ? AND content_sha256 = ?",
        (journal_entity_id, entry_anchor, content_sha256),
    ).fetchone()
    if row is None:
        return None
    return _decode_row(row)


def lookup_latest_for_anchor(
    conn: sqlite3.Connection,
    journal_entity_id: str,
    entry_anchor: str,
) -> dict[str, Any] | None:
    """Return the newest ledger row for (journal, anchor), any content hash."""
    row = conn.execute(
        "SELECT * FROM digest_ledger "
        "WHERE journal_entity_id = ? AND entry_anchor = ? "
        "ORDER BY id DESC LIMIT 1",
        (journal_entity_id, entry_anchor),
    ).fetchone()
    if row is None:
        return None
    return _decode_row(row)


def lookup_effective_watermark(
    conn: sqlite3.Connection,
    journal_entity_id: str,
    entry_anchor: str,
) -> dict[str, Any] | None:
    """Latest row with status staged or committed — watermark for skip/revision."""
    placeholders = ",".join("?" * len(_EFFECTIVE_STATUSES))
    row = conn.execute(
        "SELECT * FROM digest_ledger "
        "WHERE journal_entity_id = ? AND entry_anchor = ? "
        f"AND COALESCE(status, 'committed') IN ({placeholders}) "
        "ORDER BY id DESC LIMIT 1",
        (journal_entity_id, entry_anchor, *_EFFECTIVE_STATUSES),
    ).fetchone()
    if row is None:
        return None
    return _decode_row(row)


def lookup_pending_staged_batch(
    conn: sqlite3.Connection,
    journal_entity_id: str,
    entry_anchor: str,
    content_sha256: str,
) -> dict[str, Any] | None:
    """Return a staged ledger row for the same anchor+sha (batch idempotence)."""
    row = conn.execute(
        "SELECT * FROM digest_ledger "
        "WHERE journal_entity_id = ? AND entry_anchor = ? "
        "AND content_sha256 = ? AND COALESCE(status, 'committed') = 'staged' "
        "ORDER BY id DESC LIMIT 1",
        (journal_entity_id, entry_anchor, content_sha256),
    ).fetchone()
    if row is None:
        return None
    return _decode_row(row)


def write(
    conn: sqlite3.Connection,
    *,
    journal_entity_id: str,
    entry_anchor: str,
    content_sha256: str,
    emitted_ids: list[Any],
    staging_batch_id: str | None = None,
    verify_verdicts: dict[str, Any] | None = None,
    revision_of: int | None = None,
    status: str = "committed",
    superseded_ids: list[Any] | None = None,
    retracted_ids: list[Any] | None = None,
    carried_forward_ids: list[Any] | None = None,
    snapshot_uri: str | None = None,
) -> int:
    """Insert a digest ledger row; returns the new integer id."""
    cur = conn.execute(
        "INSERT INTO digest_ledger "
        "(journal_entity_id, entry_anchor, content_sha256, emitted_ids, "
        "staging_batch_id, verify_verdicts, revision_of, status, "
        "superseded_ids, retracted_ids, carried_forward_ids, snapshot_uri) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            journal_entity_id,
            entry_anchor,
            content_sha256,
            json_encode(emitted_ids),
            staging_batch_id,
            json_encode(verify_verdicts or {}),
            revision_of,
            status,
            json_encode(superseded_ids or []),
            json_encode(retracted_ids or []),
            json_encode(carried_forward_ids or []),
            snapshot_uri,
        ),
    )
    return int(cur.lastrowid)


def map_p_class_to_derivation_confidence(p_class: str) -> tuple[str, str]:
    """Map provenance class to (derivation_type, confidence). P2² collapses to P2."""
    normalized = p_class.strip().upper().replace("²", "2")
    if normalized in {"P1"}:
        return ("user_statement", "confirmed")
    if normalized in {"P2", "P22"}:
        return ("user_statement", "confirmed")
    if normalized in {"P3"}:
        return ("inference", "suspected")
    raise ValueError(f"unknown provenance class: {p_class!r}")


def _iso_from_month_name_text(text: str) -> str | None:
    match = _MONTH_DATE_RE.search(text)
    if not match:
        return None
    month_name, day_str, year_str = match.group(1), match.group(2), match.group(3)
    month_num = _MONTH_TO_NUM.get(month_name.lower())
    if month_num is None:
        return None
    return f"{year_str}-{month_num:02d}-{int(day_str):02d}"


def derive_valid_from_hint(claim: dict[str, Any]) -> str | None:
    """Extract ISO date (YYYY-MM-DD) from explicit fields or claim text."""
    for field in _VALID_FROM_FIELDS:
        raw = claim.get(field)
        if raw is None or raw == "":
            continue
        text = str(raw).strip()
        match = _ISO_DATE_RE.match(text)
        if match:
            return match.group(1)

    claim_text = claim.get("claim")
    if not isinstance(claim_text, str) or not claim_text.strip():
        return None

    iso_in_text = _ISO_DATE_IN_TEXT_RE.search(claim_text)
    if iso_in_text:
        return iso_in_text.group(1)

    return _iso_from_month_name_text(claim_text)


__all__ = [
    "compute_entry_content_sha256",
    "derive_valid_from_hint",
    "lookup",
    "lookup_effective_watermark",
    "lookup_latest_for_anchor",
    "lookup_pending_staged_batch",
    "map_p_class_to_derivation_confidence",
    "write",
]
