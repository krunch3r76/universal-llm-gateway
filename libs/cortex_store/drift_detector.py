"""Read-only live entity-state drift scanner (plan cortex-live-entity-state W2).

Compares card surfaces and deadline closure signals against newer structured
evidence without mutating the Cortex database.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .card import get_entity_card
from .db import check_cortex_db, cortex_conn, json_decode, query
from .models.deadlines import DeadlineItem
from .routes.deadlines import (
    _DEADLINES_SQL,
    _is_resolved,
    _resolved_assertion_ids,
)

_NEGATION_RE = re.compile(
    r"\bnot_submitted\b|\bnot submitted\b|\bunsubmitted\b", re.IGNORECASE
)
_GENERIC_TOKENS = frozenset(
    {"todo", "document", "window", "choice", "projected", "urgent"}
)
_LINK_TYPES = frozenset({"pertains_to", "references", "deadline_for", "child_of"})
_FULFILLMENT_OUTCOMES = frozenset({"met", "resolved", "completed"})
_EVIDENCE_DERIVATIONS = frozenset({"direct_observation", "user_statement"})
_LIFECYCLE_EXCLUDED = ("merged", "reaped", "deprecated", "retired")
_ASSERTIONS_SQL = """
    SELECT id, claim, confidence, derivation_type, review_status, review_notes,
           evidence_uris, created_at, observed_at, superseded_by, predicate_form
    FROM assertions
    WHERE entity_id = ?
    ORDER BY created_at ASC, id ASC
"""


@dataclass(frozen=True, slots=True)
class DriftRow:
    """One drift surface on one entity."""

    entity_id: str
    drift_class: str
    surface: str
    left_cite: str
    right_cite: str
    reason: str | None


@dataclass(frozen=True, slots=True)
class DriftScanResult:
    """Deterministic scan output."""

    rows: list[DriftRow]
    total_rows: int
    distinct_entities: int


def scan_entity_state_drift(
    *,
    conn: sqlite3.Connection | None = None,
    after_entity: Callable[[str], None] | None = None,
) -> DriftScanResult:
    """Run a full read-only drift scan; raises on connection failure or hook abort."""
    owns_conn = conn is None
    if owns_conn:
        if not check_cortex_db():
            raise RuntimeError("Cortex database unavailable")
        conn = cortex_conn(read_only=True)
    assert conn is not None
    try:
        pending: list[DriftRow] = []
        entity_ids = enumerate_entity_ids(conn)
        for entity_id in entity_ids:
            if after_entity is not None:
                after_entity(entity_id)
            card = get_entity_card(conn, entity_id=entity_id, source="boot")
            pending.extend(_detect_f1(entity_id, card))
            pending.extend(_detect_f3(conn, entity_id, card))
        for item in open_deadline_items(conn):
            pending.extend(_detect_f2(conn, item))
        return _finalize_rows(pending)
    finally:
        if owns_conn:
            conn.close()


def enumerate_entity_ids(conn: sqlite3.Connection) -> list[str]:
    """Eligible entity population (A4)."""
    table_cols = {
        row[1] for row in conn.execute("PRAGMA table_info(entities)").fetchall()
    }
    if "status" in table_cols:
        lifecycle_col = "COALESCE(status, '')"
    else:
        lifecycle_col = "COALESCE(lifecycle, '')"
    excluded = ",".join(f"'{v}'" for v in _LIFECYCLE_EXCLUDED)
    sql = (
        f"SELECT id FROM entities WHERE {lifecycle_col} NOT IN ({excluded}) "
        "ORDER BY id ASC"
    )
    rows = query(conn, sql)
    return [str(r["id"]) for r in rows]


def open_deadline_items(conn: sqlite3.Connection) -> list[DeadlineItem]:
    """Open deadlines using the same filters as ``list_deadlines(include_resolved=False)``."""
    rows = query(conn, _DEADLINES_SQL)
    deadline_ids = [str(r["deadline_id"]) for r in rows if r.get("deadline_id")]
    resolved_by_assertion = _resolved_assertion_ids(conn, deadline_ids)
    items: list[DeadlineItem] = []
    for row in rows:
        item = DeadlineItem(**row)
        if item.deadline_id is None:
            continue
        assn_resolved = item.deadline_id in resolved_by_assertion
        if _is_resolved(item) or assn_resolved:
            continue
        items.append(item)
    return items


def snapshot_table_counts(conn: sqlite3.Connection) -> dict[str, int]:
    """Row counts for every user table (write-transaction tests)."""
    tables = [
        str(r[0])
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    ]
    return {
        name: int(conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0])
        for name in sorted(tables)
    }


def _finalize_rows(rows: list[DriftRow]) -> DriftScanResult:
    seen: set[tuple[str, str, str]] = set()
    ordered: list[DriftRow] = []
    for row in sorted(
        rows, key=lambda r: (r.entity_id, r.drift_class, r.surface, r.right_cite)
    ):
        key = (row.entity_id, row.drift_class, row.surface)
        if key in seen:
            continue
        seen.add(key)
        ordered.append(row)
    entity_ids = {r.entity_id for r in ordered}
    return DriftScanResult(
        rows=ordered, total_rows=len(ordered), distinct_entities=len(entity_ids)
    )


def _domain_tokens(entity_id: str, name: str | None) -> set[str]:
    raw = f"{entity_id} {name or ''}".lower()
    parts = re.split(r"[:_\-\s]+", raw)
    return {
        p
        for p in parts
        if p and p not in _GENERIC_TOKENS and not p.isdigit() and len(p) > 1
    }


def _entity_row(conn: sqlite3.Connection, entity_id: str) -> dict[str, Any] | None:
    rows = query(
        conn,
        "SELECT id, type, name, attributes, created_at FROM entities WHERE id = ?",
        (entity_id,),
    )
    return rows[0] if rows else None


def _parse_attributes(raw: object) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        decoded = json_decode(raw, fallback={})
        return decoded if isinstance(decoded, dict) else {}
    return {}


def _has_affirmative_completion(attrs: dict[str, Any]) -> bool:
    if attrs.get("application_submitted") is True:
        return True
    if attrs.get("enrollment_confirmed") is True:
        return True
    status = attrs.get("status")
    if isinstance(status, str) and status.lower() == "submitted":
        return True
    return False


def _text_has_negation(text: str | None) -> bool:
    if not text:
        return False
    return _NEGATION_RE.search(text) is not None


def _detect_f1(entity_id: str, card: dict[str, object]) -> list[DriftRow]:
    attrs = _parse_attributes(card.get("attributes"))
    if not _has_affirmative_completion(attrs):
        return []
    rows: list[DriftRow] = []
    summary = str(card.get("summary_row") or "")
    predicate = str(card.get("predicate_summary") or "")
    for surface, text in (("summary_row", summary), ("predicate_summary", predicate)):
        if not _text_has_negation(text):
            continue
        rows.append(
            DriftRow(
                entity_id=entity_id,
                drift_class="F1",
                surface=surface,
                left_cite=text[:120] or surface,
                right_cite=_affirmative_cite(attrs),
                reason="affirmative_attributes_vs_negated_summary",
            )
        )
    return rows


def _affirmative_cite(attrs: dict[str, Any]) -> str:
    parts: list[str] = []
    if attrs.get("application_submitted") is True:
        parts.append("application_submitted=true")
    if attrs.get("enrollment_confirmed") is True:
        parts.append("enrollment_confirmed=true")
    status = attrs.get("status")
    if isinstance(status, str) and status.lower() == "submitted":
        parts.append(f"status={status}")
    return ",".join(parts) if parts else "affirmative_attributes"


def _fetch_assertions(conn: sqlite3.Connection, entity_id: str) -> list[dict[str, Any]]:
    return query(conn, _ASSERTIONS_SQL, (entity_id,))


def _claim_ts(row: dict[str, Any]) -> tuple[str, int]:
    observed = str(row.get("observed_at") or "")
    created = str(row.get("created_at") or "")
    ts = observed or created
    return (ts, int(row["id"]))


def _is_evidence_backed(row: dict[str, Any]) -> bool:
    if row.get("confidence") == "confirmed":
        return True
    dt = row.get("derivation_type")
    if dt in _EVIDENCE_DERIVATIONS:
        return True
    ev = row.get("evidence_uris")
    if ev is not None and str(ev).strip() not in ("", "[]", "null", "None"):
        return True
    return False


def _is_status_predicate(row: dict[str, Any]) -> bool:
    pf = str(row.get("predicate_form") or "")
    if pf.startswith("status("):
        return True
    claim = str(row.get("claim") or "").lower()
    return claim.startswith("status(") or " current status" in claim


def _detect_f3(
    conn: sqlite3.Connection, entity_id: str, card: dict[str, object]
) -> list[DriftRow]:
    rows: list[DriftRow] = []
    current_status = card.get("current_status")
    if not isinstance(current_status, dict):
        current_status = {}
    withheld = current_status.get("withheld_newer")
    if isinstance(withheld, list) and withheld:
        for entry in withheld:
            if not isinstance(entry, dict):
                continue
            aid = entry.get("assertion_id")
            reason = entry.get("reason")
            rows.append(
                DriftRow(
                    entity_id=entity_id,
                    drift_class="F3",
                    surface="current_status.withheld_newer",
                    left_cite=str(current_status.get("source") or "served"),
                    right_cite=f"assertion:{aid}",
                    reason=str(reason) if reason is not None else None,
                )
            )

    assertions = _fetch_assertions(conn, entity_id)
    active = [a for a in assertions if a.get("superseded_by") is None]
    served_source = current_status.get("source")
    served_ts = ("", 0)
    if served_source is not None:
        try:
            served_id = int(served_source)
        except (TypeError, ValueError):
            served_id = None
        if served_id is not None:
            for row in active:
                if int(row["id"]) == served_id:
                    served_ts = _claim_ts(row)
                    break

    for row in active:
        if not _is_status_predicate(row):
            continue
        if row.get("review_status") == "flagged":
            continue
        if not _is_evidence_backed(row):
            continue
        if _claim_ts(row) <= served_ts:
            continue
        rows.append(
            DriftRow(
                entity_id=entity_id,
                drift_class="F3",
                surface="current_status.served",
                left_cite=f"assertion:{served_source}",
                right_cite=f"assertion:{row['id']}",
                reason="newer_confirmed_status_claim",
            )
        )

    served_conf = None
    if isinstance(current_status.get("served"), dict):
        served_conf = current_status["served"].get("confidence")
    if served_conf == "believed":
        for row in active:
            if row.get("review_status") != "flagged":
                continue
            if row.get("confidence") != "confirmed":
                continue
            if not _is_evidence_backed(row):
                continue
            rows.append(
                DriftRow(
                    entity_id=entity_id,
                    drift_class="F3",
                    surface="current_status.believed_served",
                    left_cite=f"assertion:{served_source}",
                    right_cite=f"assertion:{row['id']}",
                    reason="believed_served_vs_withheld_confirmed",
                )
            )
    return rows


def _has_fulfillment_evidence(
    attrs: dict[str, Any], *, after_ts: str, assertion_rows: list[dict[str, Any]]
) -> tuple[bool, str]:
    """Return whether newer fulfillment evidence exists and a right_cite fragment."""
    if attrs.get("enrollment_confirmed") is True:
        return True, "enrollment_confirmed=true"
    if attrs.get("application_submitted") is True:
        return True, "application_submitted=true"
    outcome = attrs.get("outcome")
    if isinstance(outcome, str) and outcome in _FULFILLMENT_OUTCOMES:
        return True, f"outcome={outcome}"
    if attrs.get("resolution_status") == "resolved":
        return True, "resolution_status=resolved"
    if attrs.get("fulfillment_assertion_id") is not None:
        return True, f"fulfillment_assertion_id={attrs['fulfillment_assertion_id']}"
    for row in assertion_rows:
        if row.get("superseded_by") is not None:
            continue
        ts = _claim_ts(row)[0]
        if after_ts and ts and ts <= after_ts:
            continue
        claim_u = str(row.get("claim") or "").upper()
        if "RESOLVED" in claim_u:
            continue
        if attrs.get("enrollment_confirmed") or attrs.get("application_submitted"):
            return True, f"assertion:{row['id']}"
        if row.get("confidence") == "confirmed" and (
            "enrollment" in claim_u.lower() or "confirm" in claim_u.lower()
        ):
            return True, f"assertion:{row['id']}"
    return False, ""


def _neighbor_ids(conn: sqlite3.Connection, entity_id: str) -> set[str]:
    rows = query(
        conn,
        "SELECT from_entity, to_entity FROM relationships "
        "WHERE type IN ('pertains_to','references','deadline_for','child_of') "
        "AND (from_entity = ? OR to_entity = ?)",
        (entity_id, entity_id),
    )
    neighbors: set[str] = {entity_id}
    for row in rows:
        neighbors.add(str(row["from_entity"]))
        neighbors.add(str(row["to_entity"]))
    return neighbors


def _shared_parent_siblings(conn: sqlite3.Connection, entity_id: str) -> set[str]:
    parent_rows = query(
        conn,
        "SELECT from_entity AS parent_id FROM relationships "
        "WHERE to_entity = ? AND type IN ('pertains_to','references','deadline_for','child_of') "
        "UNION "
        "SELECT to_entity FROM relationships "
        "WHERE from_entity = ? AND type IN ('pertains_to','references','deadline_for','child_of')",
        (entity_id, entity_id),
    )
    siblings: set[str] = set()
    for prow in parent_rows:
        parent_id = str(prow["parent_id"])
        parent_tokens = _domain_tokens(parent_id, parent_id)
        if parent_tokens <= _GENERIC_TOKENS or not parent_tokens:
            continue
        sib_rows = query(
            conn,
            "SELECT from_entity AS eid FROM relationships WHERE to_entity = ? "
            "AND type IN ('pertains_to','references','deadline_for','child_of') "
            "UNION "
            "SELECT to_entity FROM relationships WHERE from_entity = ? "
            "AND type IN ('pertains_to','references','deadline_for','child_of')",
            (parent_id, parent_id),
        )
        for srow in sib_rows:
            siblings.add(str(srow["eid"]))
    siblings.discard(entity_id)
    return siblings


def _detect_f2(conn: sqlite3.Connection, item: DeadlineItem) -> list[DriftRow]:
    deadline_id = item.deadline_id
    if not deadline_id:
        return []
    deadline_row = _entity_row(conn, deadline_id)
    if deadline_row is None:
        deadline_row = _entity_row(conn, item.matter_id)
    deadline_created = str((deadline_row or {}).get("created_at") or "")
    deadline_tokens = _domain_tokens(
        deadline_id, item.deadline_name or item.matter_name
    )
    candidates: set[str] = set()
    candidates.update(_neighbor_ids(conn, item.matter_id))
    candidates.update(_shared_parent_siblings(conn, item.matter_id))
    if deadline_id != item.matter_id:
        candidates.update(_neighbor_ids(conn, deadline_id))
    rows: list[DriftRow] = []
    for cand_id in sorted(candidates):
        cand = _entity_row(conn, cand_id)
        if cand is None:
            continue
        cand_tokens = _domain_tokens(cand_id, str(cand.get("name") or ""))
        if not deadline_tokens & cand_tokens:
            continue
        attrs = _parse_attributes(cand.get("attributes"))
        assertions = _fetch_assertions(conn, cand_id)
        has_evidence, cite_tail = _has_fulfillment_evidence(
            attrs, after_ts=deadline_created, assertion_rows=assertions
        )
        if not has_evidence:
            continue
        rows.append(
            DriftRow(
                entity_id=deadline_id,
                drift_class="F2",
                surface="deadline_list",
                left_cite=deadline_id,
                right_cite=f"{cand_id}:{cite_tail}" if cite_tail else cand_id,
                reason="open_deadline_with_newer_fulfillment",
            )
        )
    return rows
