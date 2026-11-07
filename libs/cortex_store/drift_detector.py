from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from . import db as _db
from .card import get_entity_card
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
_DEAD = ("merged", "reaped", "deprecated", "retired")
_REL_TYPES = "('pertains_to','references','deadline_for','child_of')"
_REL_ON = f"type IN {_REL_TYPES} AND COALESCE(active,1)=1"
_ASQL = (
    "SELECT id,claim,confidence,derivation_type,review_status,review_notes,"
    "evidence_uris,created_at,observed_at,superseded_by,predicate_form "
    "FROM assertions WHERE entity_id=? ORDER BY created_at ASC,id ASC"
)


@dataclass(frozen=True, slots=True)
class DriftRow:
    entity_id: str
    drift_class: str
    surface: str
    left_cite: str
    right_cite: str
    reason: str | None


@dataclass(frozen=True, slots=True)
class DriftScanResult:
    rows: list[DriftRow]
    total_rows: int
    distinct_entities: int


def scan_entity_state_drift(
    *,
    conn: sqlite3.Connection | None = None,
    after_entity: Callable[[str], None] | None = None,
) -> DriftScanResult:
    owns = conn is None
    if owns:
        if not _db.check_cortex_db():
            raise RuntimeError("Cortex database unavailable")
        conn = _db.cortex_conn(read_only=True)
    assert conn is not None
    try:
        out: list[DriftRow] = []
        for eid in enumerate_entity_ids(conn):
            if after_entity:
                after_entity(eid)
            card = get_entity_card(conn, entity_id=eid, source="boot")
            out.extend(_f1(eid, card))
            out.extend(_f3(conn, eid, card))
        for item in open_deadline_items(conn):
            out.extend(_f2(conn, item))
        return _pack(out)
    finally:
        if owns:
            conn.close()


def enumerate_entity_ids(conn: sqlite3.Connection) -> list[str]:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(entities)")}
    col = "COALESCE(status,'')" if "status" in cols else "COALESCE(lifecycle,'')"
    dead = ",".join(f"'{v}'" for v in _DEAD)
    return [
        str(r["id"])
        for r in query(
            conn, f"SELECT id FROM entities WHERE {col} NOT IN ({dead}) ORDER BY id"
        )
    ]


def open_deadline_items(conn: sqlite3.Connection) -> list[DeadlineItem]:
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


def _pack(rows: list[DriftRow]) -> DriftScanResult:
    seen, ordered = set(), []
    for r in sorted(rows, key=lambda x: (x.entity_id, x.drift_class, x.surface)):
        k = (r.entity_id, r.drift_class, r.surface)
        if k not in seen:
            seen.add(k)
            ordered.append(r)
    return DriftScanResult(ordered, len(ordered), len({r.entity_id for r in ordered}))


def _attrs(raw: object) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        d = json_decode(raw, fallback={})
        return d if isinstance(d, dict) else {}
    return {}


def _true(attrs: dict[str, Any], key: str) -> bool:
    v = attrs.get(key)
    return v is True or (isinstance(v, str) and v.lower() == "true")


def _f1(eid: str, card: dict[str, object]) -> list[DriftRow]:
    attrs = _attrs(card.get("attributes"))
    if not (
        _true(attrs, "application_submitted")
        or _true(attrs, "enrollment_confirmed")
        or (isinstance(attrs.get("status"), str) and attrs["status"].lower() == "submitted")
    ):
        return []
    out: list[DriftRow] = []
    for surface, text in (
        ("summary_row", str(card.get("summary_row") or "")),
        ("predicate_summary", str(card.get("predicate_summary") or "")),
    ):
        if not text or not _NEGATION_RE.search(text):
            continue
        cite = ",".join(
            k
            for k, ok in (
                ("application_submitted", _true(attrs, "application_submitted")),
                ("enrollment_confirmed", _true(attrs, "enrollment_confirmed")),
            )
            if ok
        )
        out.append(
            DriftRow(eid, "F1", surface, text[:120], cite or "affirmative_attributes", None)
        )
    return out


def _row(conn: sqlite3.Connection, eid: str) -> dict[str, Any] | None:
    rs = query(
        conn,
        "SELECT id,name,attributes,created_at,updated_at FROM entities WHERE id=?",
        (eid,),
    )
    return rs[0] if rs else None


def _ck(row: dict[str, Any]) -> tuple[str, int]:
    return (str(row.get("created_at") or ""), int(row["id"]))


def _backed(a: dict[str, Any]) -> bool:
    ev = str(a.get("evidence_uris") or "").strip()
    return a.get("confidence") == "confirmed" and (
        a.get("derivation_type") in _DERIV or ev not in ("", "[]", "null", "None")
    )


def _after(later: str | None, earlier: str | None) -> bool:
    if not later or not earlier:
        return False
    lo, ea = later.strip(), earlier.strip()
    if " " in lo and "T" not in lo:
        lo = lo.replace(" ", "T", 1)
    if " " in ea and "T" not in ea:
        ea = ea.replace(" ", "T", 1)
    try:
        return datetime.fromisoformat(lo.replace("Z", "+00:00")) > datetime.fromisoformat(
            ea.replace("Z", "+00:00")
        )
    except ValueError:
        return lo > ea


def _f3(conn: sqlite3.Connection, eid: str, card: dict[str, object]) -> list[DriftRow]:
    out: list[DriftRow] = []
    cs = card.get("current_status")
    cs = cs if isinstance(cs, dict) else {}
    withheld = cs.get("withheld_newer")
    if isinstance(withheld, list) and withheld:
        entry = withheld[0] if isinstance(withheld[0], dict) else {}
        out.append(
            DriftRow(
                eid,
                "F3",
                "current_status.withheld_newer",
                str(cs.get("source") or "served"),
                f"assertion:{entry.get('assertion_id')}",
                str(entry.get("reason")) if entry.get("reason") is not None else None,
            )
        )
    active = [
        a for a in query(conn, _ASQL, (eid,)) if a.get("superseded_by") is None
    ]
    served_key = ("", 0)
    served_src = cs.get("source")
    served_row: dict[str, Any] | None = None
    if served_src is not None:
        try:
            sid = int(served_src)
        except (TypeError, ValueError):
            sid = None
        if sid is not None:
            for a in active:
                if int(a["id"]) == sid:
                    served_row, served_key = a, _ck(a)
                    break
    for a in active:
        if not str(a.get("predicate_form") or "").startswith("status("):
            continue
        if a.get("review_status") == "flagged" or not _backed(a) or _ck(a) <= served_key:
            continue
        out.append(
            DriftRow(
                eid,
                "F3",
                "current_status.served",
                f"assertion:{served_src}",
                f"assertion:{a['id']}",
                "newer_confirmed_status_claim",
            )
        )
    served = cs.get("served") if isinstance(cs.get("served"), dict) else {}
    grounded = served_row and (
        served_row.get("derivation_type") in _DERIV
        or str(served_row.get("evidence_uris") or "").strip()
        not in ("", "[]", "null", "None")
    )
    if served.get("confidence") == "believed" and not grounded:
        for a in active:
            if (
                a.get("review_status") != "flagged"
                or not str(a.get("predicate_form") or "").startswith("status(")
                or not _backed(a)
                or _ck(a) <= served_key
            ):
                continue
            out.append(
                DriftRow(
                    eid,
                    "F3",
                    "current_status.believed_served",
                    f"assertion:{served_src}",
                    f"assertion:{a['id']}",
                    "believed_served_vs_withheld_confirmed",
                )
            )
            break
    return out


def _tokens(eid: str, name: str | None) -> set[str]:
    parts = re.split(r"[:_\-\s]+", f"{eid} {name or ''}".lower())
    return {p for p in parts if p and p not in _GENERIC and not p.isdigit() and len(p) > 1}


def _f2_cands(conn: sqlite3.Connection, eid: str) -> set[str]:
    cands = {eid}
    q = f"SELECT from_entity a,to_entity b FROM relationships WHERE {_REL_ON} AND (from_entity=? OR to_entity=?)"
    for r in query(conn, q, (eid, eid)):
        cands.update((str(r["a"]), str(r["b"])))
    pq = f"SELECT from_entity pid FROM relationships WHERE to_entity=? AND {_REL_ON} UNION SELECT to_entity pid FROM relationships WHERE from_entity=? AND {_REL_ON}"
    for p in query(conn, pq, (eid, eid)):
        pid = str(p["pid"])
        if not _tokens(pid, pid):
            continue
        sq = f"SELECT from_entity x FROM relationships WHERE to_entity=? AND {_REL_ON} UNION SELECT to_entity x FROM relationships WHERE from_entity=? AND {_REL_ON}"
        for r in query(conn, sq, (pid, pid)):
            cands.add(str(r["x"]))
    return cands


def _f2(conn: sqlite3.Connection, item: DeadlineItem) -> list[DriftRow]:
    did = item.deadline_id
    if not did:
        return []
    drow = _row(conn, did) or _row(conn, item.matter_id)
    after = str((drow or {}).get("created_at") or "")
    dtoks = _tokens(did, item.deadline_name or item.matter_name)
    cands = _f2_cands(conn, item.matter_id)
    if did != item.matter_id:
        cands |= _f2_cands(conn, did)
    out: list[DriftRow] = []
    for cid in sorted(cands):
        ent = _row(conn, cid)
        if not ent or not (dtoks & _tokens(cid, str(ent.get("name") or ""))):
            continue
        attrs = _attrs(ent.get("attributes"))
        if not any(_after(str(ent.get(k) or ""), after) for k in ("created_at", "updated_at")):
            continue
        if _true(attrs, "enrollment_confirmed"):
            tail = "enrollment_confirmed=true"
        elif _true(attrs, "application_submitted"):
            tail = "application_submitted=true"
        elif isinstance(attrs.get("outcome"), str) and attrs["outcome"] in _OUTCOMES:
            tail = f"outcome={attrs['outcome']}"
        elif attrs.get("resolution_status") == "resolved":
            tail = "resolution_status=resolved"
        elif attrs.get("fulfillment_assertion_id") is not None:
            tail = f"fulfillment_assertion_id={attrs['fulfillment_assertion_id']}"
        else:
            continue
        out.append(
            DriftRow(
                did,
                "F2",
                "deadline_list",
                did,
                f"{cid}:{tail}" if tail else cid,
                "open_deadline_with_newer_fulfillment",
            )
        )
    return out


if __name__ == "__main__":
    _r = scan_entity_state_drift()
    print(f"distinct_entities={_r.distinct_entities} total_rows={_r.total_rows}")
