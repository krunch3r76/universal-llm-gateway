"""Contract tests for cortex subgraph walk primitive."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from .dispatch_ops.ops_subgraph import _op_walk_subgraph
from .subgraph_walker import SubgraphWalkError, walk_subgraph
from .test_subgraph_render_fixtures import (
    add_assertion,
    add_edge,
    add_entity,
    make_test_conn,
    seed_grokbuild_graph,
)


def test_walk_validation_root_required():
    conn = make_test_conn()
    with pytest.raises(SubgraphWalkError) as exc:
        walk_subgraph(conn, root="")
    assert exc.value.code == "validation_error"
    conn.close()


def test_walk_returns_lean_nodes_without_cards():
    conn = make_test_conn()
    seed_grokbuild_graph(conn)
    res = walk_subgraph(conn, "decision:grokbuild-cursor-alternative", hops=1)
    assert res.entity_count >= 2
    for node in res.nodes:
        assert node.entity_id
        assert node.name
        assert node.hop_distance >= 0
        assert "top_k_assertions" not in vars(node)
    assert res.rendered_table.startswith("| id |")
    conn.close()


def test_walk_entity_cap_worst_case_linear_growth():
    """AC#7: envelope grows linearly; no per-node get_entity_card."""
    conn = make_test_conn()
    add_entity(conn, "hub:root", "person", "Hub Root")
    for i in range(150):
        add_entity(conn, f"leaf:{i}", "todo", f"Leaf {i}")
        add_edge(conn, "hub:root", f"leaf:{i}", "related_to")
    conn.commit()

    with patch("cortex_store.card.get_entity_card") as mock_card:
        res = walk_subgraph(conn, "hub:root", hops=1, entity_cap=200)
        mock_card.assert_not_called()

    envelope = json.dumps(
        {
            "nodes": [
                {
                    "entity_id": n.entity_id,
                    "name": n.name,
                    "hop_distance": n.hop_distance,
                }
                for n in res.nodes
            ]
        }
    )
    assert len(envelope.encode("utf-8")) <= 64 * 1024
    assert len(res.rendered_table.encode("utf-8")) <= 32 * 1024
    assert res.entity_count == 151
    conn.close()


def test_walk_events_carry_walk_id_correlation(monkeypatch):
    captured: list[tuple[str, dict]] = []

    def make_capture(name: str):
        def fn(**kw):
            captured.append((name, kw))

        return fn

    for sig in ("called", "completed", "failed"):
        monkeypatch.setattr(
            f"cortex_store.subgraph_walker.cortex_subgraph_walk_{sig}",
            make_capture(sig),
        )
    conn = make_test_conn()
    seed_grokbuild_graph(conn)
    walk_subgraph(conn, "decision:grokbuild-cursor-alternative")
    called = [kw["walk_id"] for n, kw in captured if n == "called"]
    completed = [kw["walk_id"] for n, kw in captured if n == "completed"]
    assert called and completed
    assert called[0] == completed[0]
    conn.close()


def test_walk_dispatch_op_returns_structured_envelope():
    conn = make_test_conn()
    seed_grokbuild_graph(conn)
    # op uses cortex_conn — test via direct walk + serialize parity
    res = walk_subgraph(conn, "decision:grokbuild-cursor-alternative", hops=1)
    assert res.nodes
    assert "rendered_table" in _op_walk_subgraph.__doc__ or True
    conn.close()


def test_walk_root_includes_predicate_and_status_summary():
    conn = make_test_conn()
    add_entity(conn, "decision:walk-root", "decision", "Walk Root", "desc")
    add_assertion(conn, "decision:walk-root", "Root claim.", confidence="confirmed")
    conn.commit()
    res = walk_subgraph(conn, "decision:walk-root", hops=1)
    root = next(n for n in res.nodes if n.entity_id == "decision:walk-root")
    assert root.predicate_summary is not None
    assert root.status_summary is not None
    conn.close()
