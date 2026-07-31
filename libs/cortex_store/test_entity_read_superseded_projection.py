"""entity_get superseded breadcrumb projection — AC-1/2/3/5/6."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime

import pytest
from fastapi import HTTPException

from cortex_store._intent_card_test_fixtures import insert_entity
from cortex_store.dispatch_ops.ops_entities import _op_entity_get
from cortex_store.entity_read import get_entity_impl
from cortex_store.routes.entities import _resolve_entity_get_historical

_ENTITY_ID = "todo:superseded-projection-fixture"
_ENRICHMENT = {
    "prospective_summary": "x" * 800,
    "events_json": json.dumps([{"t": i, "msg": "event" * 40} for i in range(12)]),
    "reasoning_summary": "y" * 600,
    "evidence": "z" * 400,
}


def _insert_enriched_assertion(
    conn: sqlite3.Connection,
    *,
    entity_id: str,
    claim: str,
    confidence: str = "believed",
    superseded_by: int | None = None,
    attributes: str | None = None,
) -> int:
    now = datetime.now(UTC).isoformat()
    cur = conn.execute(
        "INSERT INTO assertions ("
        "entity_id, claim, confidence, evidence, evidence_uris, derivation_type, "
        "reasoning_summary, prospective_summary, events_json, superseded_by, "
        "attributes, created_at"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            entity_id,
            claim,
            confidence,
            _ENRICHMENT["evidence"],
            json.dumps(["cortex://evidence/a", "cortex://evidence/b"]),
            "inference",
            _ENRICHMENT["reasoning_summary"],
            _ENRICHMENT["prospective_summary"],
            _ENRICHMENT["events_json"],
            superseded_by,
            attributes,
            now,
        ),
    )
    conn.commit()
    return int(cur.lastrowid or 0)


def _seed_byte_gate_fixture(conn: sqlite3.Connection) -> None:
    insert_entity(conn, entity_id=_ENTITY_ID, entity_type="todo")
    active_ids: list[int] = []
    for i in range(22):
        active_ids.append(
            _insert_enriched_assertion(
                conn,
                entity_id=_ENTITY_ID,
                claim=f"Active operative claim #{i:02d} with enrichment payload.",
            )
        )
    for i, successor_id in enumerate(active_ids[:22]):
        attrs: str | None = None
        if i % 5 == 0:
            attrs = json.dumps({"revision_type": "correction"})
        elif i % 5 == 1:
            attrs = json.dumps({"revision_type": "restatement"})
        elif i % 5 == 2:
            attrs = json.dumps({"revision_type": "status_update"})
        if attrs:
            conn.execute(
                "UPDATE assertions SET attributes = ? WHERE id = ?",
                (attrs, successor_id),
            )
            conn.commit()
        _insert_enriched_assertion(
            conn,
            entity_id=_ENTITY_ID,
            claim=(
                f"Superseded claim #{i:02d} material delta "
                if i % 7 == 0
                else f"Superseded restatement claim #{i:02d} identical."
            ),
            superseded_by=successor_id,
        )


def _compact_json(payload: dict) -> bytes:
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()


def test_ac1_lean_default_byte_gate_and_semantics(
    migrated_conn: sqlite3.Connection,
) -> None:
    conn = migrated_conn
    _seed_byte_gate_fixture(conn)

    lean = get_entity_impl(conn, entity_id=_ENTITY_ID, include_superseded=False)
    historical = get_entity_impl(conn, entity_id=_ENTITY_ID, include_superseded=True)

    lean_bytes = len(_compact_json(lean))
    historical_bytes = len(_compact_json(historical))
    assert lean_bytes <= 0.60 * historical_bytes, (
        f"lean={lean_bytes} historical={historical_bytes} "
        f"ratio={lean_bytes / historical_bytes:.2f}"
    )

    lean_active = {(a["id"], a["claim"]) for a in lean["assertions"]}
    hist_active = {
        (a["id"], a["claim"])
        for a in historical["assertions"]
        if a.get("superseded_by") is None
    }
    assert lean_active == hist_active

    breadcrumb = lean["superseded_breadcrumb"]
    assert breadcrumb is not None
    assert breadcrumb["count"] == 22
    assert len(breadcrumb["ids"]) == 22

    superseded_ids = {
        a["id"] for a in historical["assertions"] if a.get("superseded_by") is not None
    }
    assert not superseded_ids.intersection({a["id"] for a in lean["assertions"]})

    corrections = lean.get("superseded_corrections") or []
    correction_rows = [
        r
        for r in conn.execute(
            "SELECT a.id, s.attributes FROM assertions a "
            "JOIN assertions s ON a.superseded_by = s.id "
            "WHERE a.entity_id = ? AND a.superseded_by IS NOT NULL",
            (_ENTITY_ID,),
        ).fetchall()
        if json.loads(r["attributes"] or "{}").get("revision_type") == "correction"
    ]
    correction_ids = {int(r["id"]) for r in correction_rows}
    emitted_ids = {c["id"] for c in corrections}
    assert correction_ids.issubset(emitted_ids)


def test_ac2_historical_opt_in_matches_legacy_full(
    migrated_conn: sqlite3.Connection,
) -> None:
    conn = migrated_conn
    _seed_byte_gate_fixture(conn)

    via_flag = get_entity_impl(conn, entity_id=_ENTITY_ID, include_superseded=True)
    assert via_flag.get("superseded_breadcrumb") is None
    assert via_flag.get("superseded_corrections") is None
    assert len(via_flag["assertions"]) == 44


def test_ac5_legacy_unclassified_material_and_immaterial(
    migrated_conn: sqlite3.Connection,
) -> None:
    conn = migrated_conn
    entity_id = "todo:legacy-unclassified"
    insert_entity(conn, entity_id=entity_id, entity_type="todo")

    successor_id = _insert_enriched_assertion(
        conn, entity_id=entity_id, claim="Successor claim unchanged."
    )
    immaterial_id = _insert_enriched_assertion(
        conn,
        entity_id=entity_id,
        claim="Successor claim unchanged.",
        superseded_by=successor_id,
    )

    material_successor = _insert_enriched_assertion(
        conn,
        entity_id=entity_id,
        claim="Successor with different claim.",
        confidence="confirmed",
    )
    material_id = _insert_enriched_assertion(
        conn,
        entity_id=entity_id,
        claim="Predecessor with different claim.",
        confidence="believed",
        superseded_by=material_successor,
    )

    payload = get_entity_impl(conn, entity_id=entity_id, include_superseded=False)
    correction_ids = {c["id"] for c in (payload.get("superseded_corrections") or [])}
    assert material_id in correction_ids
    assert immaterial_id not in correction_ids
    assert (
        payload["superseded_breadcrumb"]["by_revision_type"].get(
            "legacy_unclassified", 0
        )
        >= 2
    )


def test_ac6_invalid_intent_param_combos() -> None:
    with pytest.raises(HTTPException) as exc_info:
        _resolve_entity_get_historical(
            intent="full-historical",
            include_superseded=False,
            include_superseded_present=True,
        )
    assert exc_info.value.status_code == 400

    with pytest.raises(HTTPException):
        _resolve_entity_get_historical(
            intent="card",
            include_superseded=True,
            include_superseded_present=True,
        )

    dispatch = _op_entity_get(
        entity_id="todo:any",
        intent="full-historical",
        include_superseded=True,
    )
    assert dispatch.get("status_code") == 400
    assert "Invalid combo" in dispatch["error"]
