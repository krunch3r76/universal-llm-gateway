#!/usr/bin/env python3
"""Read-only step-3 contradiction detection for agent-bus thread 1210 (Bucket-G fenced).

Throwaway helper — not an apply script. Writes JSON summary to /tmp for sidecar assembly.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path("/mnt/torus/projects/universal-llm-gateway")
sys.path.insert(0, str(ROOT / "scripts/cortex"))
sys.path.insert(0, str(ROOT / "libs"))

from cortex_store.polarity import detect_polarity_conflict  # noqa: E402
from predicate_form.parser import Predicate, PredicateParseError, parse  # noqa: E402
from reconstruct_constants import MARKER  # noqa: E402
from reconstruct_disposition_fenced_review import (  # noqa: E402
    BUCKET_G_FENCED_SQL,
    is_fenced,
)

DB = Path.home() / ".cortex" / "cortex.db"
OUT = Path("/tmp/1210-step3-analysis.json")

BOE_FLAG_ENTITIES = frozenset(
    {
        "person:kaywan-mansubi",
        "account:chase-mortgage-8787",
        "legal_matter:estate-of-fred-mansubi-24pr197054",
        "estate:fred-mansubi",
        "estate:mary-mansubi",
        "legal_matter:life-insurance-sale",
    }
)


@dataclass
class Row:
    id: int
    entity_id: str
    claim: str
    predicate_form: str | None


def _parse_row(r: Row) -> Predicate | None:
    if not r.predicate_form:
        return None
    try:
        return parse(r.predicate_form.strip())
    except PredicateParseError:
        return None


def _subject_key(p: Predicate) -> tuple[str, str]:
    subj = p.args[0] if p.args else ""
    return (p.name, subj)


def _value_tail(p: Predicate) -> tuple[str, ...]:
    return p.args[1:]


def _tail_str(args: tuple[str, ...]) -> str:
    return ", ".join(args).lower()


_STATUS_INCOMPATIBLE: dict[str, frozenset[str]] = {
    "current": frozenset(
        {
            "former",
            "expired",
            "inactive",
            "terminated",
            "closed",
            "past_due",
            "delinquent",
        }
    ),
    "former": frozenset({"current", "active"}),
    "active": frozenset({"inactive", "closed", "terminated"}),
}


def _status_incompatible(tail_a: tuple[str, ...], tail_b: tuple[str, ...]) -> bool:
    a, b = _tail_str(tail_a), _tail_str(tail_b)
    for token, bad in _STATUS_INCOMPATIBLE.items():
        if token in a and any(x in b for x in bad):
            return True
        if token in b and any(x in a for x in bad):
            return True
    return detect_polarity_conflict(a, b)


def predicate_conflicts(staged: Row, committed: Row) -> str | None:
    sp = _parse_row(staged)
    cp = _parse_row(committed)
    if sp and cp:
        if sp.name != cp.name:
            return None
        if not sp.args or not cp.args:
            return None
        if sp.args[0] != cp.args[0]:
            return None
        ta, tb = _value_tail(sp), _value_tail(cp)
        if ta == tb:
            return None
        if sp.name == "status" and _status_incompatible(ta, tb):
            return f"status incompatible: {ta!r} vs {tb!r}"
        if detect_polarity_conflict(_tail_str(ta), _tail_str(tb)):
            return f"predicate polarity: {ta!r} vs {tb!r}"
        return None
    if detect_polarity_conflict(staged.claim, committed.claim):
        return "claim polarity conflict"
    return None


def committed_count(conn: sqlite3.Connection, entity_id: str) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM assertions WHERE entity_id=? "
        "AND superseded_by IS NULL AND review_status='committed'",
        (entity_id,),
    ).fetchone()[0]


def load_committed(conn: sqlite3.Connection, entity_id: str) -> list[Row]:
    rows = conn.execute(
        "SELECT id, entity_id, claim, predicate_form FROM assertions "
        "WHERE entity_id=? AND superseded_by IS NULL AND review_status='committed'",
        (entity_id,),
    ).fetchall()
    return [
        Row(int(r[0]), str(r[1]), str(r[2] or ""), r[3] and str(r[3]) or None)
        for r in rows
    ]


def main() -> int:
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    bucket = [
        dict(r)
        for r in conn.execute(BUCKET_G_FENCED_SQL, (MARKER,)).fetchall()
        if is_fenced(str(r["entity_id"]))
    ]
    assertion_count = len(bucket)
    entity_ids = sorted({str(r["entity_id"]) for r in bucket})
    entity_count = len(entity_ids)

    supplement_only_ids: list[int] = []
    supplement_only_entities: list[dict] = []
    entity_tables: list[dict] = []
    all_contradictions: list[dict] = []
    total_supplements = 0

    for eid in entity_ids:
        ids = [int(r["id"]) for r in bucket if str(r["entity_id"]) == eid]
        pf_by_id = {}
        if ids:
            q = "SELECT id, claim, predicate_form FROM assertions WHERE id IN ({})".format(
                ",".join("?" * len(ids))
            )
            for row in conn.execute(q, ids):
                pf_by_id[int(row[0])] = (
                    str(row[1] or ""),
                    row[2] and str(row[2]) or None,
                )
        staged_rows = [Row(aid, eid, pf_by_id[aid][0], pf_by_id[aid][1]) for aid in ids]
        cc = committed_count(conn, eid)
        if cc == 0:
            ids = [r.id for r in staged_rows]
            supplement_only_ids.extend(ids)
            supplement_only_entities.append(
                {"entity_id": eid, "staged_count": len(ids), "assertion_ids": ids}
            )
            continue

        committed_rows = load_committed(conn, eid)
        entity_contra: list[dict] = []
        supplements = 0
        staged_with_conflict: set[int] = set()
        for staged in staged_rows:
            for committed in committed_rows:
                reason = predicate_conflicts(staged, committed)
                if reason:
                    staged_with_conflict.add(staged.id)
                    entity_contra.append(
                        {
                            "entity_id": eid,
                            "staged_id": staged.id,
                            "committed_id": committed.id,
                            "reason": reason,
                            "staged_predicate": staged.predicate_form,
                            "committed_predicate": committed.predicate_form,
                        }
                    )
        supplements += len(staged_rows) - len(staged_with_conflict)

        total_supplements += supplements
        all_contradictions.extend(entity_contra)
        notes = []
        if eid in BOE_FLAG_ENTITIES:
            notes.append("BOE-2026-09-15 focus")
        if entity_contra:
            notes.append(f"{len(entity_contra)} candidate-contradiction(s)")
        entity_tables.append(
            {
                "entity_id": eid,
                "staged_count": len(staged_rows),
                "committed_active": cc,
                "candidate_contradictions": len(entity_contra),
                "supplements": supplements,
                "notes": "; ".join(notes) if notes else "",
                "contradiction_rows": entity_contra,
            }
        )

    conn.close()

    payload = {
        "live_snapshot": {
            "assertion_count": assertion_count,
            "entity_count": entity_count,
            "marker": MARKER,
        },
        "partition": {
            "committed_status_zero_entities": len(supplement_only_entities),
            "committed_status_zero_assertion_ids": len(supplement_only_ids),
            "committed_status_positive_entities": entity_count
            - len(supplement_only_entities),
        },
        "totals": {
            "candidate_contradictions": len(all_contradictions),
            "supplements_committed_positive": total_supplements,
            "supplement_only_assertion_ids": len(supplement_only_ids),
        },
        "supplement_only_ids": sorted(supplement_only_ids),
        "supplement_only_entities": supplement_only_entities,
        "entity_tables": entity_tables,
        "candidate_contradictions": all_contradictions,
    }
    OUT.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload["live_snapshot"], indent=2))
    print(json.dumps(payload["partition"], indent=2))
    print(json.dumps(payload["totals"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
