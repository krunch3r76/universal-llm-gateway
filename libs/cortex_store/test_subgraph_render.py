"""Contract tests for cortex subgraph render V1.1 (review-fixes pass).

Rewritten from the grok V1.1 dispatch tests, which accepted a stub renderer
because their assertions checked only "function returned something."
This rev asserts the real contract \u2014 see ``test_subgraph_render_fixtures``
for the shared DB setup.
"""

from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from .dispatch_ops.ops_subgraph import _op_render_subgraph
from .main import create_app
from .subgraph_renderer import SubgraphRenderError, render_subgraph
from .test_subgraph_render_fixtures import (
    add_assertion,
    add_edge,
    add_entity,
    init_temp_db,
    make_test_conn,
    seed_grokbuild_graph,
)

_ROOT = "decision:grokbuild-cursor-alternative"


# --- Validation surface (parametrized) ---


@pytest.mark.parametrize(
    "kwargs,reason_field",
    [
        ({"root": ""}, "root"),
        ({"root": _ROOT, "hops": 4}, "hops"),
        ({"root": _ROOT, "top_k_assertions": 51}, "top_k_assertions"),
        ({"root": _ROOT, "edge_types": ["foo"]}, "edge_types"),
    ],
)
def test_validation_failures_emit_field_specific_reason(kwargs, reason_field):
    conn = make_test_conn()
    seed_grokbuild_graph(conn)
    with pytest.raises(SubgraphRenderError) as exc:
        render_subgraph(conn, **kwargs)
    assert exc.value.code == "validation_error"
    assert exc.value.data["field"] == reason_field
    conn.close()


def test_root_not_found_returns_404_code():
    conn = make_test_conn()
    seed_grokbuild_graph(conn)
    with pytest.raises(SubgraphRenderError) as exc:
        render_subgraph(conn, "decision:nonexistent")
    assert exc.value.code == "entity_not_found"
    assert exc.value.status == 404
    conn.close()


# --- Hops is a hard BFS depth bound ---


def test_hops_bound_strict_at_1():
    """hops=1 must not reach a hop-2 node."""
    conn = make_test_conn()
    seed_grokbuild_graph(conn)
    add_entity(conn, "todo:v1-child", "todo", "V1 Child")
    add_edge(conn, "todo:grokbuild-v1", "todo:v1-child", "depends_on")
    conn.commit()
    res = render_subgraph(conn, _ROOT, hops=1)
    ids = {e.entity_id for e in res.entities}
    assert "todo:v1-child" not in ids
    conn.close()


def test_hops_bound_strict_at_2():
    """hops=2 visits hops 0/1/2 only, not hop 3 on a 5-node chain."""
    conn = make_test_conn()
    for i in range(5):
        add_entity(conn, f"n{i}", "n", f"N{i}")
        if i > 0:
            add_edge(conn, f"n{i - 1}", f"n{i}", "depends_on")
    conn.commit()
    res = render_subgraph(conn, "n0", hops=2)
    ids = {e.entity_id for e in res.entities}
    assert ids == {"n0", "n1", "n2"}
    conn.close()


def test_entity_cap_only_fires_within_hop_bound():
    """Cap fires only when breadth (60-leaf star at hop 1) overruns it."""
    conn = make_test_conn()
    add_entity(conn, "root", "n", "Root")
    for i in range(60):
        add_entity(conn, f"leaf{i}", "n", f"L{i}")
        add_edge(conn, "root", f"leaf{i}", "depends_on")
    conn.commit()
    with pytest.raises(SubgraphRenderError) as exc:
        render_subgraph(conn, "root", hops=1)
    assert exc.value.code == "subgraph_too_large"
    conn.close()


# --- Induced edge set (LLM-first deviation from V1.1 spec) ---


def test_induced_subgraph_captures_sibling_edges():
    """Sibling edge v1->v2 must appear in edges[] even though BFS reached
    v2 directly from root (not via v1)."""
    conn = make_test_conn()
    seed_grokbuild_graph(conn)
    res = render_subgraph(conn, _ROOT, hops=1)
    idents = {(e.source_id, e.target_id, e.type_id) for e in res.edges}
    assert ("todo:grokbuild-v1", "todo:grokbuild-v2", "depends_on") in idents
    conn.close()


def test_direction_from_root_uses_hop_comparison():
    conn = make_test_conn()
    seed_grokbuild_graph(conn)
    res = render_subgraph(conn, _ROOT, hops=1)
    sibling = next(
        e
        for e in res.edges
        if e.source_id == "todo:grokbuild-v1" and e.target_id == "todo:grokbuild-v2"
    )
    assert sibling.direction_from_root == "cross"
    out = next(e for e in res.edges if e.source_id == _ROOT)
    assert out.direction_from_root == "outbound"
    inb = next(e for e in res.edges if e.target_id == _ROOT)
    assert inb.direction_from_root == "inbound"
    conn.close()


def test_self_loops_filtered_from_edge_set():
    conn = make_test_conn()
    seed_grokbuild_graph(conn)
    add_entity(conn, "X", "x", "X")
    add_edge(conn, "X", "X", "related_to")
    conn.commit()
    res = render_subgraph(conn, "X", hops=1)
    assert all(e.source_id != e.target_id for e in res.edges)
    conn.close()


def test_archived_entities_excluded_from_traversal():
    """Source of an active archives_to edge is not visited."""
    conn = make_test_conn()
    seed_grokbuild_graph(conn)
    add_entity(conn, "archived:foo", "todo", "Archived", workflow_state="done")
    add_entity(conn, "archive:bucket", "archive", "Bucket")
    add_edge(conn, _ROOT, "archived:foo", "depends_on")
    add_edge(conn, "archived:foo", "archive:bucket", "archives_to")
    conn.commit()
    res = render_subgraph(conn, _ROOT, hops=1)
    ids = {e.entity_id for e in res.entities}
    assert "archived:foo" not in ids
    conn.close()


def test_edge_type_filter_excludes_unfiltered_paths():
    conn = make_test_conn()
    seed_grokbuild_graph(conn)
    res = render_subgraph(conn, _ROOT, edge_types=["depends_on"])
    ids = {e.entity_id for e in res.entities}
    assert "decision:other" not in ids


# --- Markdown contract (spec template skeleton) ---


def test_markdown_contains_spec_skeleton():
    conn = make_test_conn()
    seed_grokbuild_graph(conn)
    md = render_subgraph(conn, _ROOT, hops=1).rendered
    for needle in (
        "# Grokbuild Cursor Alt",
        "**Type:** decision",
        "**Status:** confirmed",
        "## Active Assertions (top 7)",
        "**[confirmed]** Root claim active.",
        "## Related Entities (5 found, 1-hop)",
        "### depends_on",
        "### references",
        "\u2192",  # outbound arrow
        "\u2190",  # inbound arrow
        "**Hop:** 1",
    ):
        assert needle in md, f"missing from markdown: {needle!r}"
    conn.close()


def test_markdown_renders_empty_sentinel_when_no_related():
    conn = make_test_conn()
    add_entity(conn, "todo:alone", "todo", "Alone", "No links.", workflow_state="open")
    conn.commit()
    res = render_subgraph(conn, "todo:alone", hops=1)
    assert "_(no related entities)_" in res.rendered
    conn.close()


def test_markdown_deterministic_byte_stable():
    conn = make_test_conn()
    seed_grokbuild_graph(conn)
    r1 = render_subgraph(conn, _ROOT, hops=1).rendered
    r2 = render_subgraph(conn, _ROOT, hops=1).rendered
    assert r1 == r2
    conn.close()


def test_markdown_escapes_user_content_headings():
    """User description starting with '#' is defanged."""
    conn = make_test_conn()
    add_entity(conn, "decision:weird", "decision", "Weird", "## injected heading")
    conn.commit()
    md = render_subgraph(conn, "decision:weird", hops=1).rendered
    assert "\\## injected heading" in md
    structural = [line for line in md.split("\n") if line.startswith("## ")]
    expected = {
        "## Active Assertions (top 7)",
        "## Related Entities (0 found, 1-hop)",
    }
    assert all(h in expected for h in structural), structural
    conn.close()


# --- include_superseded scope ---


def test_include_superseded_toggle():
    conn = make_test_conn()
    seed_grokbuild_graph(conn)
    off = render_subgraph(conn, _ROOT, include_superseded=False)
    on = render_subgraph(conn, _ROOT, include_superseded=True)
    off_claims = [
        a["claim"]
        for e in off.entities
        if e.entity_id == _ROOT
        for a in e.card.get("top_k_assertions", [])
    ]
    on_claims = [
        a["claim"]
        for e in on.entities
        if e.entity_id == _ROOT
        for a in e.card.get("top_k_assertions", [])
    ]
    assert not any("Superseded" in c for c in off_claims)
    assert any("Superseded" in c for c in on_claims)
    conn.close()


# --- Event correlation ---


def _patch_event_captures(monkeypatch):
    captured: list[tuple[str, dict]] = []

    def make_capture(name: str):
        def fn(**kw):
            captured.append((name, kw))

        return fn

    for sig in ("called", "completed", "failed"):
        monkeypatch.setattr(
            f"cortex_store.subgraph_renderer.cortex_subgraph_render_{sig}",
            make_capture(sig),
        )
    return captured


def test_events_carry_render_id_correlation(monkeypatch):
    captured = _patch_event_captures(monkeypatch)
    conn = make_test_conn()
    seed_grokbuild_graph(conn)
    render_subgraph(conn, _ROOT)
    called = [kw["render_id"] for n, kw in captured if n == "called"]
    completed = [kw["render_id"] for n, kw in captured if n == "completed"]
    assert called and completed
    assert called[0] == completed[0]
    conn.close()


def test_failed_event_uses_field_specific_reason(monkeypatch):
    captured = _patch_event_captures(monkeypatch)
    conn = make_test_conn()
    seed_grokbuild_graph(conn)
    with pytest.raises(SubgraphRenderError):
        render_subgraph(conn, _ROOT, hops=4)
    failed = [kw for n, kw in captured if n == "failed"]
    assert failed and failed[0]["reason"] == "hops_out_of_range"
    conn.close()


# --- Route <-> op envelope parity ---


def test_dispatch_op_returns_error_key_on_validation_failure(tmp_path, monkeypatch):
    """Sibling-ops convention: {'error': ...} so friction-hint attaches."""
    db_path = tmp_path / "test.db"
    init_temp_db(str(db_path))
    monkeypatch.setattr("cortex_store.db._CORTEX_DB", db_path)
    out = _op_render_subgraph(root="", hops=1)
    assert "error" in out
    assert out["code"] == "validation_error"


def test_route_and_op_envelope_parity(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    conn = make_test_conn(str(db_path))
    add_entity(conn, "decision:x", "decision", "X", "d")
    add_entity(conn, "todo:y", "todo", "Y", "d", workflow_state="open")
    add_edge(conn, "decision:x", "todo:y", "depends_on")
    conn.commit()
    conn.close()
    monkeypatch.setattr("cortex_store.db._CORTEX_DB", db_path)
    op_result = _op_render_subgraph(root="decision:x", hops=1)
    app = create_app(db_path=str(db_path))
    client = TestClient(app)
    resp = client.get("/subgraph/render", params={"root": "decision:x"})
    assert resp.status_code == 200
    route_result = resp.json()
    assert op_result.keys() == route_result.keys()
    for key in op_result:
        if key == "generated_at":
            continue
        assert op_result[key] == route_result[key], f"mismatch on {key}"


def test_route_returns_validation_envelope_shape(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    init_temp_db(str(db_path))
    monkeypatch.setattr("cortex_store.db._CORTEX_DB", db_path)
    app = create_app(db_path=str(db_path))
    client = TestClient(app)
    resp = client.get("/subgraph/render", params={"root": "", "hops": 1})
    assert resp.status_code == 422
    body = resp.json()
    assert body["code"] == "validation_error"
    assert body["source"] == "cortex-api"
    assert body["data"]["field"] == "root"


# --- V1.5 polish: state signals block + provenance flags ---


def test_v15_state_signals_block_emitted_when_predicate_summary_nonempty():
    """Edge-derived predicate_summary surfaces as ## State signals block."""
    conn = make_test_conn()
    seed_grokbuild_graph(conn)
    md = render_subgraph(conn, _ROOT, hops=1).rendered
    # seed_grokbuild_graph has outgoing depends_on + inbound references
    # edges, so Tier 2 synthesis yields a non-empty predicate_summary.
    assert "## State signals" in md
    assert "depends_on(" in md
    # Block must precede Active Assertions in the rendered order.
    assert md.index("## State signals") < md.index("## Active Assertions")
    conn.close()


def test_v15_state_signals_block_omitted_when_empty():
    """Solo entity with no edges/archives has empty predicate_summary; block omitted."""
    conn = make_test_conn()
    add_entity(conn, "decision:isolated", "decision", "Isolated", "No links.")
    conn.commit()
    md = render_subgraph(conn, "decision:isolated", hops=1).rendered
    assert "## State signals" not in md
    conn.close()


def test_v15_provenance_flag_primary_source_backed():
    """Assertion with URI-shaped evidence_uris gets [primary-source-backed]."""
    conn = make_test_conn()
    add_entity(conn, "decision:src", "decision", "Sourced", "d")
    add_assertion(
        conn,
        "decision:src",
        "Source-backed claim.",
        confidence="confirmed",
        derivation_type="agent_observation",
        evidence_uris=["cortex://notes/legal/exhibit-3.md"],
    )
    conn.commit()
    md = render_subgraph(conn, "decision:src", hops=1).rendered
    assert "**[confirmed]** [primary-source-backed] Source-backed claim." in md
    conn.close()


def test_v15_provenance_flag_verbatim_quote_beats_uri():
    """derivation_type='quotation' takes precedence over URI-shape check."""
    conn = make_test_conn()
    add_entity(conn, "decision:quo", "decision", "Quoted", "d")
    add_assertion(
        conn,
        "decision:quo",
        "Verbatim claim.",
        confidence="confirmed",
        derivation_type="quotation",
        evidence_uris=["cortex://notes/exhibit.md"],
    )
    conn.commit()
    md = render_subgraph(conn, "decision:quo", hops=1).rendered
    assert "[verbatim-quote]" in md
    assert "[primary-source-backed]" not in md
    conn.close()


def test_v15_provenance_flag_derived_for_inference():
    """derivation_type='inference' without URI evidence gets [derived]."""
    conn = make_test_conn()
    add_entity(conn, "decision:inf", "decision", "Inferred", "d")
    add_assertion(
        conn,
        "decision:inf",
        "Inferred claim.",
        confidence="believed",
        derivation_type="inference",
    )
    conn.commit()
    md = render_subgraph(conn, "decision:inf", hops=1).rendered
    assert "**[believed]** [derived] Inferred claim." in md
    conn.close()


def test_v15_provenance_flag_observation_class_no_flag():
    """direct_observation / agent_observation / user_statement carry no flag."""
    conn = make_test_conn()
    add_entity(conn, "decision:obs", "decision", "Observed", "d")
    add_assertion(
        conn,
        "decision:obs",
        "Observed claim.",
        confidence="confirmed",
        derivation_type="direct_observation",
    )
    conn.commit()
    md = render_subgraph(conn, "decision:obs", hops=1).rendered
    assert "**[confirmed]** Observed claim." in md
    # No bracketed flag between **[confirmed]** and the claim.
    assert "[primary-source-backed]" not in md
    assert "[derived]" not in md
    assert "[verbatim-quote]" not in md
    conn.close()


def test_v15_uri_shape_rejects_free_text_evidence():
    """evidence_uris with free-text (not URI-shaped) doesn't trigger flag."""
    conn = make_test_conn()
    add_entity(conn, "decision:freetext", "decision", "Freetext", "d")
    add_assertion(
        conn,
        "decision:freetext",
        "Loosely-evidenced claim.",
        confidence="confirmed",
        derivation_type="agent_observation",
        evidence_uris=["various sources", "session context"],
    )
    conn.commit()
    md = render_subgraph(conn, "decision:freetext", hops=1).rendered
    assert "[primary-source-backed]" not in md
    assert "**[confirmed]** Loosely-evidenced claim." in md
    conn.close()


def test_v15_uri_shape_accepts_absolute_path():
    """Absolute path (starts with /) qualifies as URI-shaped."""
    conn = make_test_conn()
    add_entity(conn, "decision:abspath", "decision", "Abspath", "d")
    add_assertion(
        conn,
        "decision:abspath",
        "File-backed claim.",
        confidence="confirmed",
        derivation_type="agent_observation",
        evidence_uris=["/data/files/exhibit.pdf"],
    )
    conn.commit()
    md = render_subgraph(conn, "decision:abspath", hops=1).rendered
    assert "[primary-source-backed]" in md
    conn.close()


def test_v15_confidence_extraction_regex_unbroken():
    """Provenance flag placement outside the bold preserves confidence-regex stability."""
    import re

    conn = make_test_conn()
    add_entity(conn, "decision:re", "decision", "Regex", "d")
    add_assertion(
        conn,
        "decision:re",
        "Some claim.",
        confidence="confirmed",
        derivation_type="agent_observation",
        evidence_uris=["cortex://notes/x.md"],
    )
    conn.commit()
    md = render_subgraph(conn, "decision:re", hops=1).rendered
    # Canonical downstream parser pattern: capture confidence between **[ and ]**.
    matches = re.findall(r"\*\*\[(\w+)\]\*\*", md)
    assert "confirmed" in matches
    conn.close()


def test_v15_provenance_flag_renders_on_related_entity_top_assertion():
    """Per-related-entity 'Top assertion:' line also carries the provenance flag."""
    conn = make_test_conn()
    add_entity(conn, "decision:root", "decision", "Root", "r")
    add_entity(conn, "todo:child", "todo", "Child", "c", workflow_state="open")
    add_edge(conn, "decision:root", "todo:child", "depends_on")
    add_assertion(
        conn,
        "todo:child",
        "Child claim.",
        confidence="believed",
        derivation_type="quotation",
        evidence_uris=["cortex://exhibit.md"],
    )
    conn.commit()
    md = render_subgraph(conn, "decision:root", hops=1).rendered
    assert "**Top assertion:** [believed] [verbatim-quote] Child claim." in md
    conn.close()


# Inline guard against unused import flake \u2014 TestClient + sqlite3 are used above.
_ = (TestClient, sqlite3)
