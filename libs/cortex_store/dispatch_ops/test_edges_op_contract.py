"""Contract tests for `edge_create`, `edges`, and `edge_update` dispatch ops (a:39610, a:39611)."""

from __future__ import annotations

from pathlib import Path

import pytest

from cortex_store.dispatch_ops.ops_edges import (
    _op_edge_create,
    _op_edge_retire,
    _op_edge_update,
    _op_edges,
)

_VALID_SESSION = "grok-bot-2026-10-18-032900-b48"


@pytest.fixture()
def edges_db(migrated_db_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from cortex_store import db as cortex_db

    monkeypatch.setattr(cortex_db, "_CORTEX_DB", migrated_db_path)
    with cortex_db.cortex_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO entities (id, type, name) VALUES (?, 'service', ?)",
            ("service:mcp-server", "mcp-server"),
        )
        conn.execute(
            "INSERT OR IGNORE INTO assertions (id, entity_id, claim, confidence, confidence_score) "
            "VALUES (101, 'service:mcp-server', '[tool_error] first', 'believed', 0.7), "
            "       (102, 'service:mcp-server', '[tool_error] second', 'believed', 0.7)"
        )
        conn.commit()
    return migrated_db_path


@pytest.mark.offline
def test_op_edge_create_empty_returns_all_five_missing_fields() -> None:
    res = _op_edge_create()
    assert res.get("status_code") == 422
    err = res["error"]
    assert err["error"] == "missing_required_fields"
    missing_names = [e["field"] for e in err["errors"]]
    assert missing_names == ["session_id", "agent", "from_node", "to_node", "edge_type"]


@pytest.mark.offline
def test_op_edge_create_grammar_error_includes_status_code_and_grok_bot_seat_hint() -> None:
    res = _op_edge_create(
        session_id="2026-10-18-gc1",
        agent="grok-bot-gc1",
        from_node="assertion:101",
        to_node="assertion:102",
        edge_type="same_class_as",
    )
    assert res["status_code"] == 422
    assert res["reason"] == "session_id_grammar"
    assert res["seat_hint"] == "mint via cortex_brief(seat='grok-bot')"


@pytest.mark.offline
def test_op_edges_unknown_filter_key_returns_422_with_valid_filters() -> None:
    res = _op_edges(node_id="assertion:101")
    assert res["status_code"] == 422
    err = res["error"]
    assert err["error"] == "unknown_filter_keys"
    assert err["unknown_keys"] == ["node_id"]
    assert "node" in err["valid_filters"]
    assert "from_node" in err["valid_filters"]
    assert "to_node" in err["valid_filters"]
    assert "not node_id" in err["hint"]


@pytest.mark.offline
def test_op_edges_node_matches_either_endpoint_and_forwards_filters(edges_db: Path) -> None:
    created = _op_edge_create(
        session_id=_VALID_SESSION,
        agent="grok-bot-gc1",
        from_node="assertion:101",
        to_node="assertion:102",
        edge_type="same_class_as",
    )
    assert "error" not in created, created
    edge_id = int(created["id"])

    by_from = _op_edges(node="assertion:101")
    by_to = _op_edges(node="assertion:102")
    assert [e["id"] for e in by_from["items"]] == [edge_id]
    assert [e["id"] for e in by_to["items"]] == [edge_id]

    excluded = _op_edges(node="assertion:101", edge_type_exclude="same_class_as")
    assert excluded["items"] == []

    by_agent = _op_edges(agent="grok-bot-gc1", session_id=_VALID_SESSION, since_hours=1)
    assert [e["id"] for e in by_agent["items"]] == [edge_id]

    _op_edge_retire(edge_id=edge_id)
    active_only = _op_edges(node="assertion:101")
    assert active_only["items"] == []
    with_retired = _op_edges(node="assertion:101", include_retired=True)
    assert [e["id"] for e in with_retired["items"]] == [edge_id]


@pytest.mark.offline
def test_op_edge_update_provenance_immutable_and_unknown_key_guards(edges_db: Path) -> None:
    res_imm = _op_edge_update(
        edge_id=1,
        session_id="grok-bot-2026-10-18-032900-b48",
        agent="grok-bot",
    )
    assert res_imm["status_code"] == 422
    err = res_imm["error"]
    assert err["error"] == "provenance_immutable"
    assert err["fields"] == ["agent", "session_id"]
    assert "edge_retire" in err["hint"]

    res_unk = _op_edge_update(edge_id=1, bogus_field="x")
    assert res_unk["status_code"] == 422
    assert res_unk["error"]["error"] == "unknown_filter_keys"
    assert res_unk["error"]["unknown_keys"] == ["bogus_field"]
