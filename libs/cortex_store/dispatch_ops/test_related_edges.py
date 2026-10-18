"""Unit and SQLite integration tests for `related=[…]` on `friction` and `assert` (a:39610)."""

from __future__ import annotations

from pathlib import Path

import pytest

from cortex_store.dispatch_ops._related_edges import prepare_related_edges
from cortex_store.dispatch_ops.ops_assertions_friction import _op_friction
from cortex_store.dispatch_ops.ops_assertions_write import _op_assert
from cortex_store.dispatch_ops.ops_edges import _op_edges

_VALID_SESSION = "cursor-2026-10-18-032900-a01"


@pytest.fixture()
def related_db(migrated_db_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from cortex_store import db as cortex_db

    monkeypatch.setattr(cortex_db, "_CORTEX_DB", migrated_db_path)
    with cortex_db.cortex_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO entities (id, type, name) VALUES (?, 'service', ?)",
            ("service:mcp-server", "mcp-server"),
        )
        conn.commit()
    return migrated_db_path


@pytest.mark.offline
def test_friction_with_related_assertion_id_creates_edge(related_db: Path) -> None:
    target = _op_friction(
        owner="service:mcp-server",
        category="tool_error",
        note="first friction row",
        agent="cursor",
        session_id=_VALID_SESSION,
    )
    assert "error" not in target, target
    target_id = int(target["item"]["id"])

    created = _op_friction(
        owner="service:mcp-server",
        category="tool_error",
        note="second friction row in same class",
        agent="cursor",
        session_id=_VALID_SESSION,
        related=[
            {
                "assertion_id": target_id,
                "edge_type": "same_class_as",
                "strength": 0.9,
                "context": "both hit the same edge contract gap",
            }
        ],
    )
    assert "error" not in created, created
    new_id = int(created["item"]["id"])
    assert "related_edges" in created
    assert len(created["related_edges"]) == 1
    edge_item = created["related_edges"][0]
    assert edge_item["from_node"] == f"assertion:{new_id}"
    assert edge_item["to_node"] == f"assertion:{target_id}"
    assert edge_item["edge_type"] == "same_class_as"
    assert edge_item["session_id"] == _VALID_SESSION

    listed = _op_edges(node=f"assertion:{new_id}")
    assert "error" not in listed, listed
    assert len(listed["items"]) == 1
    assert listed["items"][0]["to_node"] == f"assertion:{target_id}"


@pytest.mark.offline
def test_assert_with_related_to_node_defaults_edge_type_relates_to(related_db: Path) -> None:
    created = _op_assert(
        entity_id="service:mcp-server",
        claim="MCP server exposes edge contract in surface description across code and life mounts",
        confidence="believed",
        evidence="verified in services/mcp-server/tools/cortex_named_tools/_surface_render.py",
        evidence_uris=["cortex://notes/system/threads/15851-plan-cortex-link-and-grok-bot-seat-fable.md"],
        seeded_by="cursor",
        derivation_type="agent_observation",
        confidence_score=0.75,
        session_id=_VALID_SESSION,
        related=[{"to_node": "service:mcp-server"}],
    )
    assert "error" not in created, created
    new_id = int(created["item"]["id"])
    assert len(created["related_edges"]) == 1
    edge_item = created["related_edges"][0]
    assert edge_item["from_node"] == f"assertion:{new_id}"
    assert edge_item["to_node"] == "service:mcp-server"
    assert edge_item["edge_type"] == "relates_to"
    assert edge_item["strength"] == pytest.approx(0.8)


@pytest.mark.offline
def test_assert_dry_run_with_related_returns_preview_without_inserting(related_db: Path) -> None:
    from cortex_store import db as cortex_db

    res = _op_assert(
        entity_id="service:mcp-server",
        claim="Dry-run assertion with related edge preview verified before database write",
        confidence="believed",
        evidence="verified in libs/cortex_store/dispatch_ops/ops_assertions_write.py",
        evidence_uris=["cortex://notes/system/threads/15851-plan-cortex-link-and-grok-bot-seat-fable.md"],
        seeded_by="cursor",
        derivation_type="agent_observation",
        confidence_score=0.75,
        session_id=_VALID_SESSION,
        dry_run=True,
        related=[{"to_node": "service:mcp-server", "edge_type": "duplicates", "strength": 0.75}],
    )
    assert "error" not in res, res
    assert res["dry_run"] is True
    assert res["related_preview"] == [
        {
            "to_node": "service:mcp-server",
            "edge_type": "duplicates",
            "strength": 0.75,
            "context": None,
        }
    ]
    with cortex_db.cortex_conn() as conn:
        edge_count = conn.execute("SELECT COUNT(*) FROM session_edges").fetchone()[0]
        assertion_count = conn.execute("SELECT COUNT(*) FROM assertions").fetchone()[0]
    assert edge_count == 0
    assert assertion_count == 0


@pytest.mark.offline
def test_related_without_session_id_fails_before_assertion_insert(related_db: Path) -> None:
    from cortex_store import db as cortex_db

    res = _op_friction(
        owner="service:mcp-server",
        category="tool_error",
        note="missing session_id with related",
        agent="cursor",
        related=[{"to_node": "service:mcp-server"}],
    )
    assert res.get("status_code") == 422
    assert res.get("reason") == "session_id_grammar"
    assert res.get("seat_hint") == "mint via cortex_brief(seat='cursor')"

    with cortex_db.cortex_conn() as conn:
        count = conn.execute("SELECT COUNT(*) FROM assertions").fetchone()[0]
    assert count == 0


@pytest.mark.offline
def test_related_with_invalid_session_id_grammar_returns_seat_hint(related_db: Path) -> None:
    from cortex_store import db as cortex_db

    res = _op_friction(
        owner="service:mcp-server",
        category="tool_error",
        note="bad session grammar with related",
        agent="grok-bot-gc1",
        session_id="2026-10-18-gc1",
        related=[{"to_node": "service:mcp-server"}],
    )
    assert res.get("status_code") == 422
    assert res.get("reason") == "session_id_grammar"
    assert res.get("seat_hint") == "mint via cortex_brief(seat='grok-bot')"

    with cortex_db.cortex_conn() as conn:
        count = conn.execute("SELECT COUNT(*) FROM assertions").fetchone()[0]
    assert count == 0


@pytest.mark.offline
def test_related_with_unknown_edge_type_fails_before_insert(related_db: Path) -> None:
    from cortex_store import db as cortex_db

    res = _op_friction(
        owner="service:mcp-server",
        category="tool_error",
        note="unknown edge_type in related",
        agent="cursor",
        session_id=_VALID_SESSION,
        related=[{"to_node": "service:mcp-server", "edge_type": "not_a_registered_edge_type"}],
    )
    assert res.get("status_code") == 422
    err = res["error"]
    assert "Unknown edge_type" in err["error"]
    assert err["field"] == "related[0].edge_type"
    assert "same_class_as" in err["valid_types"]
    assert "duplicates" in err["valid_types"]

    with cortex_db.cortex_conn() as conn:
        count = conn.execute("SELECT COUNT(*) FROM assertions").fetchone()[0]
    assert count == 0


@pytest.mark.offline
def test_related_with_missing_target_assertion_fails_before_insert(related_db: Path) -> None:
    from cortex_store import db as cortex_db

    res = _op_friction(
        owner="service:mcp-server",
        category="tool_error",
        note="missing target assertion id",
        agent="cursor",
        session_id=_VALID_SESSION,
        related=[{"assertion_id": 999999, "edge_type": "same_class_as"}],
    )
    assert res.get("status_code") == 422
    err = res["error"]
    assert err["reason"] == "dangling_edge"
    assert err["missing_nodes"] == ["assertion:999999"]

    with cortex_db.cortex_conn() as conn:
        count = conn.execute("SELECT COUNT(*) FROM assertions").fetchone()[0]
    assert count == 0


@pytest.mark.offline
def test_related_with_both_or_neither_target_keys_rejects(related_db: Path) -> None:
    from cortex_store import db as cortex_db

    with cortex_db.cortex_conn() as conn:
        prepared_neither, err_neither = prepare_related_edges(
            conn,
            [{"edge_type": "relates_to"}],
            session_id=_VALID_SESSION,
            agent="cursor",
        )
        prepared_both, err_both = prepare_related_edges(
            conn,
            [{"assertion_id": 1, "to_node": "assertion:1"}],
            session_id=_VALID_SESSION,
            agent="cursor",
        )
    assert prepared_neither is None and err_neither is not None
    assert err_neither["status_code"] == 422
    assert err_neither["error"]["field"] == "related[0]"
    assert prepared_both is None and err_both is not None
    assert err_both["status_code"] == 422
    assert err_both["error"]["field"] == "related[0]"
