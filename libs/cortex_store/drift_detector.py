"""Read-only live entity-state drift scanner (plan cortex-live-entity-state W2)."""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from . import db as _db
from .card import _fetch_status_current_rows, get_entity_card
from .card_temporal import _parse_instant
from .db import json_decode, query
from .models.deadlines import DeadlineItem
from .routes.deadlines import (
    _DEADLINES_SQL,
    _is_resolved,
    _resolved_assertion_ids,
)

_NEGATION_RE = re.compile(
    r"\bnot_submitted\b|\bnot submitted\b|\bunsubmitted\b", re.IGNORECASE
)
_GENERIC = frozenset({"todo", "document", "window", "choice", "projected", "urgent"})
_OUTCOMES = frozenset({"met", "resolved", "completed"})
_DERIV = frozenset({"direct_observation", "user_statement"})
_FULFILL_STATUS = frozenset({"resolved", "fulfilled"})
_DEAD = ("merged", "reaped", "deprecated", "retired")
_REL_ON = (
    "type IN ('pertains_to','references','deadline_for','child_of') "
    "AND COALESCE(active,1)=1"
)
_F2_ASSERTION_SQL = (
    "SELECT id,created_at,observed_at,resolution_status,fulfillment_assertion_id "
    "FROM assertions WHERE entity_id=? AND superseded_by IS NULL "
    "ORDER BY created_at ASC,id ASC"
)


@dataclass(frozen=True, slots=True)
class DriftRow:
    """One detected drift surface on one entity."""

    entity_id: str
    drift_class: str
    surface: str
    left_cite: str
    right_cite: str
    reason: str | None


@dataclass(frozen=True, slots=True)
class DriftScanResult:
    """Deterministic scan output and headline counts."""

    rows: list[DriftRow]
    total_rows: int
    distinct_entities: int


def scan_entity_state_drift(
    *,
    conn: sqlite3.Connection | None = None,
    after_entity: Callable[[str], None] | None = None,
) -> DriftScanResult:
    """Run a read-only drift scan; raises when Cortex is unavailable."""
    owns = conn is None
    if owns:
        if not _db.check_cortex_db():
            raise RuntimeError("Cortex database unavailable")
        conn = _db.cortex_conn(read_only=True)
    assert conn is not None
    try:
        pending: list[DriftRow] = []
        for entity_id in enumerate_entity_ids(conn):
            if after_entity:
                after_entity(entity_id)
            card = get_entity_card(conn, entity_id=entity_id, source="boot")
            pending.extend(_detect_f1(entity_id, card))
            pending.extend(_detect_f3(conn, entity_id, card))
        for item in open_deadline_items(conn):
            pending.extend(_detect_f2(conn, item))
        return _finalize_rows(pending)
    finally:
        if owns:
            conn.close()


def enumerate_entity_ids(conn: sqlite3.Connection) -> list[str]:
    """Return entity ids for active rows in deterministic id order (A4)."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(entities)")}
    col = "COALESCE(status,'')" if "status" in cols else "COALESCE(lifecycle,'')"
    dead = ",".join(f"'{v}'" for v in _DEAD)
    sql = f"SELECT id FROM entities WHERE {col} NOT IN ({dead}) ORDER BY id"
    return [str(r["id"]) for r in query(conn, sql)]


def open_deadline_items(conn: sqlite3.Connection) -> list[DeadlineItem]:
    """Open deadlines mirroring list_deadlines(include_resolved=False) filters."""
    rows = query(conn, _DEADLINES_SQL)
    resolved = _resolved_assertion_ids(
        conn, [str(r["deadline_id"]) for r in rows if r.get("deadline_id")]
    )
    out: list[DeadlineItem] = []
    for row in rows:
        item = DeadlineItem(**row)
        if item.deadline_id and not _is_resolved(item) and item.deadline_id not in resolved:
            out.append(item)
    return out


def _cli_field(value: object) -> str:
    return ("" if value is None else str(value)).replace("\t", " ").replace("\n", " ").replace("\r", " ")


def format_cli_lines(result: DriftScanResult) -> list[str]:
    """Format drift rows and headline totals for printed baseline output."""
    lines = [
        "\t".join(map(_cli_field, (r.drift_class, r.entity_id, r.surface, r.left_cite, r.right_cite, r.reason or "")))
        for r in result.rows
    ]
    lines.append(
        f"distinct_entities={result.distinct_entities} total_rows={result.total_rows}"
    )
    return lines


def _finalize_rows(rows: list[DriftRow]) -> DriftScanResult:
    seen, ordered = set(), []
    for row in sorted(rows, key=lambda r: (r.entity_id, r.drift_class, r.surface)):
        key = (row.entity_id, row.drift_class, row.surface)
        if key not in seen:
            seen.add(key)
            ordered.append(row)
    return DriftScanResult(ordered, len(ordered), len({r.entity_id for r in ordered}))


def _parse_attributes(raw: object) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        decoded = json_decode(raw, fallback={})
        return decoded if isinstance(decoded, dict) else {}
    return {}


def _attribute_true(attrs: dict[str, Any], key: str) -> bool:
    value = attrs.get(key)
    return value is True or (isinstance(value, str) and value.lower() == "true")


def _claim_order_key(row: dict[str, Any]) -> tuple[str, int]:
    return (str(row.get("created_at") or ""), int(row["id"]))


def _instant_after(later: str | None, earlier: str | None) -> bool:
    if not later or not earlier:
        return False
    try:
        return _parse_instant(later) > _parse_instant(earlier)
    except ValueError:
        return False


def _evidence_backed(row: dict[str, Any]) -> bool:
    evidence = str(row.get("evidence_uris") or "").strip()
    return row.get("confidence") == "confirmed" and (
        row.get("derivation_type") in _DERIV
        or evidence not in ("", "[]", "null", "None")
    )


def _detect_f1(entity_id: str, card: dict[str, object]) -> list[DriftRow]:
    attrs = _parse_attributes(card.get("attributes"))
    if not (
        _attribute_true(attrs, "application_submitted")
        or _attribute_true(attrs, "enrollment_confirmed")
        or (isinstance(attrs.get("status"), str) and attrs["status"].lower() == "submitted")
    ):
        return []
    out: list[DriftRow] = []
    for surface, text in (
        ("summary_row", str(card.get("summary_row") or "")),
        ("predicate_summary", str(card.get("predicate_summary") or "")),
    ):
        if text and _NEGATION_RE.search(text):
            cite = ",".join(
                k
                for k, ok in (
                    ("application_submitted", _attribute_true(attrs, "application_submitted")),
                    ("enrollment_confirmed", _attribute_true(attrs, "enrollment_confirmed")),
                )
                if ok
            )
            out.append(
                DriftRow(entity_id, "F1", surface, text[:120], cite or "affirmative_attributes", None)
            )
    return out


def _entity_row(conn: sqlite3.Connection, entity_id: str) -> dict[str, Any] | None:
    rows = query(
        conn,
        "SELECT id,name,attributes,created_at FROM entities WHERE id=?",
        (entity_id,),
    )
    return rows[0] if rows else None


def _domain_tokens(entity_id: str, name: str | None) -> set[str]:
    parts = re.split(r"[:_\-\s]+", f"{entity_id} {name or ''}".lower())
    return {p for p in parts if p and p not in _GENERIC and not p.isdigit() and len(p) > 1}


def _f2_candidates(conn: sqlite3.Connection, entity_id: str) -> set[str]:
    candidates = {entity_id}
    edge_sql = f"SELECT from_entity a,to_entity b FROM relationships WHERE {_REL_ON} AND (from_entity=? OR to_entity=?)"
    for row in query(conn, edge_sql, (entity_id, entity_id)):
        candidates.update((str(row["a"]), str(row["b"])))
    parent_sql = (
        f"SELECT from_entity pid FROM relationships WHERE to_entity=? AND {_REL_ON} UNION "
        f"SELECT to_entity pid FROM relationships WHERE from_entity=? AND {_REL_ON}"
    )
    child_sql = (
        f"SELECT from_entity x FROM relationships WHERE to_entity=? AND {_REL_ON} UNION "
        f"SELECT to_entity x FROM relationships WHERE from_entity=? AND {_REL_ON}"
    )
    for parent in query(conn, parent_sql, (entity_id, entity_id)):
        parent_id = str(parent["pid"])
        if _domain_tokens(parent_id, parent_id):
            for child in query(conn, child_sql, (parent_id, parent_id)):
                candidates.add(str(child["x"]))
    return candidates


def _f2_cite_tail(
    conn: sqlite3.Connection, ent: dict[str, Any], candidate_id: str, after: str
) -> str | None:
    attrs = _parse_attributes(ent.get("attributes"))
    if _instant_after(str(ent.get("created_at") or ""), after):
        if _attribute_true(attrs, "enrollment_confirmed"):
            return f"{candidate_id}:enrollment_confirmed=true"
        if _attribute_true(attrs, "application_submitted"):
            return f"{candidate_id}:application_submitted=true"
        outcome = attrs.get("outcome")
        if isinstance(outcome, str) and outcome in _OUTCOMES:
            return f"{candidate_id}:outcome={outcome}"
        if attrs.get("resolution_status") == "resolved":
            return f"{candidate_id}:resolution_status=resolved"
        if attrs.get("fulfillment_assertion_id") is not None:
            return f"{candidate_id}:fulfillment_assertion_id={attrs['fulfillment_assertion_id']}"
    for row in query(conn, _F2_ASSERTION_SQL, (candidate_id,)):
        ts = str(row.get("observed_at") or row.get("created_at") or "")
        if not _instant_after(ts, after):
            continue
        if row.get("fulfillment_assertion_id") is not None or str(
            row.get("resolution_status") or ""
        ) in _FULFILL_STATUS:
            return f"{candidate_id}:assertion:{row['id']}"
    return None


def _detect_f2(conn: sqlite3.Connection, item: DeadlineItem) -> list[DriftRow]:
    deadline_id = item.deadline_id
    if not deadline_id:
        return []
    deadline_row = _entity_row(conn, deadline_id) or _entity_row(conn, item.matter_id)
    deadline_created = str((deadline_row or {}).get("created_at") or "")
    deadline_tokens = _domain_tokens(deadline_id, item.deadline_name or item.matter_name)
    candidates = _f2_candidates(conn, item.matter_id)
    if deadline_id != item.matter_id:
        candidates |= _f2_candidates(conn, deadline_id)
    out: list[DriftRow] = []
    for candidate_id in sorted(candidates):
        ent = _entity_row(conn, candidate_id)
        if not ent or not (deadline_tokens & _domain_tokens(candidate_id, str(ent.get("name") or ""))):
            continue
        cite_tail = _f2_cite_tail(conn, ent, candidate_id, deadline_created)
        if cite_tail is None:
            continue
        out.append(
            DriftRow(
                deadline_id, "F2", "deadline_list", deadline_id, cite_tail,
                "open_deadline_with_newer_fulfillment",
            )
        )
    return out


def _detect_f3(
    conn: sqlite3.Connection, entity_id: str, card: dict[str, object]
) -> list[DriftRow]:
    out: list[DriftRow] = []
    current_status = card.get("current_status") if isinstance(card.get("current_status"), dict) else {}
    served_source = current_status.get("source")
    left_cite = f"assertion:{served_source}" if served_source is not None else "served"
    withheld = current_status.get("withheld_newer")
    if isinstance(withheld, list) and withheld and isinstance(withheld[0], dict):
        entry = withheld[0]
        out.append(
            DriftRow(
                entity_id, "F3", "current_status.withheld_newer", left_cite,
                f"assertion:{entry.get('assertion_id')}",
                str(entry.get("reason")) if entry.get("reason") is not None else None,
            )
        )
    status_rows = _fetch_status_current_rows(conn, entity_id=entity_id)
    served_key = ("", 0)
    served_row: dict[str, Any] | None = None
    if served_source is not None:
        try:
            served_id = int(served_source)
        except (TypeError, ValueError):
            served_id = None
        if served_id is not None:
            for row in status_rows:
                if int(row["id"]) == served_id:
                    served_row, served_key = row, _claim_order_key(row)
                    break
    served = current_status.get("served") if isinstance(current_status.get("served"), dict) else {}
    served_grounded = served_row and (
        served_row.get("derivation_type") in _DERIV
        or str(served_row.get("evidence_uris") or "").strip() not in ("", "[]", "null", "None")
    )
    believed = served.get("confidence") == "believed" and not served_grounded
    if believed:
        flagged = [r for r in status_rows if r.get("review_status") == "flagged" and _evidence_backed(r)]
        if flagged:
            older = [r for r in flagged if _claim_order_key(r) < served_key]
            pick = max(older or flagged, key=_claim_order_key)
            out.append(
                DriftRow(
                    entity_id, "F3", "current_status.believed_served", left_cite,
                    f"assertion:{pick['id']}", "believed_served_vs_withheld_confirmed",
                )
            )
    for row in status_rows:
        if row.get("review_status") == "flagged" or not _evidence_backed(row):
            continue
        if served_row is None or _claim_order_key(row) <= served_key:
            continue
        out.append(
            DriftRow(
                entity_id, "F3", "current_status.served", left_cite,
                f"assertion:{row['id']}", "newer_confirmed_status_claim",
            )
        )
    return out


if __name__ == "__main__":
    result = scan_entity_state_drift()
    for line in format_cli_lines(result):
        print(line)
