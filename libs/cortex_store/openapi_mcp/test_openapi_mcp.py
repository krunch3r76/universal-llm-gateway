"""CI guards for OpenAPI-first MCP adapter (OMDR-STRANGLER-S136)."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from cortex_store.dispatch_ops import _OP_SPECS
from cortex_store.main import create_app
from cortex_store.openapi_mcp._route_map import PIPELINE_PENDING, UNTYPEABLE_OPS
from cortex_store.openapi_mcp.bijection import (
    assert_op_served_bijection,
    assert_served_bijection,
    assert_unbound_ratchet_baseline_not_stale,
    assert_unbound_ratchet_no_new_unbound,
    find_reachable_unserved_violations,
    served_operation_ids,
    unbound_ratchet_violations,
)
from cortex_store.openapi_mcp.census import build_four_bucket_census
from cortex_store.openapi_mcp.codegen import check_generated_module, dry_run_generate
from cortex_store.openapi_mcp.death_path import DEATH_PATH_GATE_DOC, death_path_gate_met
from cortex_store.openapi_mcp.generated_adapter_manifest import SERVED_OPS
from cortex_store.openapi_mcp.schema_channel import SCHEMA_CHANNEL_DEFAULT

_REPO_ROOT = Path(__file__).resolve().parents[3]

UNBOUND_BASELINE: frozenset[str] = frozenset(
    {
        "claim_alignment",
        "endeavor_dispose_row",
        "endeavor_repair_t1",
        "endeavor_write_row",
        "entities_bulk_upsert",
        "entity_retype",
        "graph_reach",
        "pinned_deliverable_write",
        "recon_sidecar_write",
        "register_skill_substrate",
        "relationships_bulk_upsert",
        "thread_sidecar_write",
        "todo_close_sidecar",
        "view_render",
    }
)


@pytest.mark.offline
def test_four_bucket_census_partitions_all_ops() -> None:
    census = build_four_bucket_census()
    assert census.total == len(_OP_SPECS)
    union = (
        census.served
        | census.rb_only
        | census.neither
        | census.untypeable
        | census.pipeline_pending
    )
    assert union == set(_OP_SPECS)


@pytest.mark.offline
def test_four_bucket_census_counts() -> None:
    census = build_four_bucket_census()
    assert census.total == len(_OP_SPECS)
    assert census.served == frozenset(SERVED_OPS)
    assert census.untypeable == UNTYPEABLE_OPS & set(_OP_SPECS)
    assert census.pipeline_pending == frozenset(PIPELINE_PENDING) & set(_OP_SPECS)
    assert census.rb_only | census.neither == UNBOUND_BASELINE


@pytest.mark.offline
def test_pipeline_pending_ops_tagged_with_slug() -> None:
    import re

    slug_re = re.compile(r"^pipelines/[a-z0-9_]+/v\d+/$")
    for op, slug in PIPELINE_PENDING.items():
        assert op in _OP_SPECS, f"{op} missing from dispatch ops"
        assert slug_re.match(slug), f"{op}: slug {slug!r} not pipelines/<name>/v<N>/"
    census = build_four_bucket_census()
    assert census.pipeline_slugs == {
        op: PIPELINE_PENDING[op] for op in census.pipeline_pending
    }


@pytest.mark.offline
def test_no_op_both_stamped_and_pipeline_pending() -> None:
    stamped = frozenset(SERVED_OPS)
    pending = frozenset(PIPELINE_PENDING)
    overlap = stamped & pending
    assert not overlap, f"ops in both SERVED_OPS and PIPELINE_PENDING: {sorted(overlap)}"


@pytest.mark.offline
def test_every_served_op_bijects() -> None:
    schema = create_app().openapi()
    assert_served_bijection(schema)


@pytest.mark.offline
def test_assert_op_openapi_bijection() -> None:
    schema = create_app().openapi()
    assert_op_served_bijection(schema)


@pytest.mark.offline
def test_generator_dry_run_covers_served_ops() -> None:
    schema = create_app().openapi()
    manifest = dry_run_generate(schema)
    assert len(manifest.served_ops) == len(SERVED_OPS)
    assert manifest.served_ops["assert"]["path"] == "/assertions"
    assert manifest.openapi_sha256


@pytest.mark.offline
def test_generated_manifest_matches_openapi() -> None:
    schema = create_app().openapi()
    assert check_generated_module(schema) is True


@pytest.mark.offline
def test_served_operation_ids_include_assert() -> None:
    ids = served_operation_ids(create_app().openapi())
    assert ids["assert"] == "create_assertion_assertions_post"


@pytest.mark.offline
def test_served_bindings_derived_from_native_route_stamps() -> None:
    """Manifest op set equals the document's own ``x-mcp`` stamps — no seed."""
    from openapi_mcp.binding import extract_typed_routes

    schema = create_app().openapi()
    derived = extract_typed_routes(schema)
    manifest = dry_run_generate(schema)
    assert set(manifest.served_ops) == set(derived)
    assert (
        manifest.served_ops["assert"]["operation_id"] == derived["assert"].operation_id
    )


@pytest.mark.offline
def test_edge_list_and_create_bindings_match_handler_direction() -> None:
    """``edges`` lists via GET /edges; ``edge_create`` creates via POST /edges."""
    from openapi_mcp.binding import extract_typed_routes

    schema = create_app().openapi()
    routes = extract_typed_routes(schema)
    assert routes["edges"].method == "GET"
    assert routes["edges"].path == "/edges"
    assert routes["edge_create"].method == "POST"
    assert routes["edge_create"].path == "/edges"


@pytest.mark.offline
def test_s6_batch1_read_get_bindings_match_proposed_paths() -> None:
    """S6 batch 1: read-only GET substrate ops use bind-proposed paths."""
    from openapi_mcp.binding import extract_typed_routes

    schema = create_app().openapi()
    routes = extract_typed_routes(schema)
    assert routes["assertion_get"].method == "GET"
    assert routes["assertion_get"].path == "/assertions/{assertion_id}"
    assert routes["assertion_state"].method == "GET"
    assert routes["assertion_state"].path == "/entities/{entity_id}/assertion-state"
    assert routes["entities_by_content_hash"].method == "GET"
    assert (
        routes["entities_by_content_hash"].path
        == "/entities/by-content-hash/{content_hash}"
    )


@pytest.mark.offline
def test_no_hand_maintained_route_seed_remains() -> None:
    """The (method, path) seed is deleted, not relocated."""
    import cortex_store.openapi_mcp._route_map as route_map

    for gone in ("mcp_route_seed", "_MCP_ROUTE_SEED", "TYPED_ROUTE_BY_OP"):
        assert not hasattr(route_map, gone), f"{gone} still present"


@pytest.mark.offline
def test_missing_stamp_is_detectable_not_silent() -> None:
    """An op whose route loses its stamp becomes enumerably unbound + fails --check.

    This is the property the deleted seed could not provide: a seed with no row
    for an op produced silence. Here the same omission (a) drops the op from the
    derived manifest, (b) lists it in ``unbound_dispatch_ops``, and (c) makes the
    committed-manifest check — i.e. ``openapi_mcp_codegen.py --check`` — fail.
    """
    from cortex_store.openapi_mcp._route_map import unbound_dispatch_ops

    schema = create_app().openapi()
    assert "assert" not in unbound_dispatch_ops(schema)
    assert check_generated_module(schema) is True

    del schema["paths"]["/assertions"]["post"]["x-mcp"]

    assert "assert" in unbound_dispatch_ops(schema)
    assert "assert" not in dry_run_generate(schema).served_ops
    assert check_generated_module(schema) is False

    # W2 S1–S4: newly stamped ops behave the same (entity_get / GET /entities/{id}).
    schema2 = create_app().openapi()
    assert "entity_get" not in unbound_dispatch_ops(schema2)
    del schema2["paths"]["/entities/{entity_id}"]["get"]["x-mcp"]
    assert "entity_get" in unbound_dispatch_ops(schema2)
    assert "entity_get" not in dry_run_generate(schema2).served_ops
    assert check_generated_module(schema2) is False


@pytest.mark.offline
def test_unbound_ops_enumerate_the_strangler_gap() -> None:
    """Every dispatch op is served, exempt, or listed as unbound — none invisible."""
    from cortex_store.openapi_mcp._route_map import UNTYPEABLE_OPS, unbound_dispatch_ops

    schema = create_app().openapi()
    unbound = frozenset(unbound_dispatch_ops(schema))
    census = build_four_bucket_census(openapi_schema=schema)
    assert unbound == census.rb_only | census.neither
    assert unbound & census.served == frozenset()
    assert unbound & UNTYPEABLE_OPS == frozenset()


@pytest.mark.offline
def test_new_op_without_a_stamp_shows_up_unbound() -> None:
    """Adding a dispatch op without stamping a route is caught, not absorbed."""
    from cortex_store.openapi_mcp._route_map import unbound_dispatch_ops

    schema = create_app().openapi()
    specs = {"assert": "…", "brand_new_op": "…"}
    assert unbound_dispatch_ops(schema, op_specs=specs) == ["brand_new_op"]


@pytest.mark.offline
def test_death_path_gate_requires_both_conditions() -> None:
    assert death_path_gate_met(served_parity=True, zero_non_adapter_traffic=True)
    assert not death_path_gate_met(served_parity=False, zero_non_adapter_traffic=True)
    assert not death_path_gate_met(served_parity=True, zero_non_adapter_traffic=False)
    assert "served-parity" in DEATH_PATH_GATE_DOC


@pytest.mark.offline
def test_schema_channel_defaults_to_generated_op() -> None:
    assert SCHEMA_CHANNEL_DEFAULT == "cortex_schema(op)"


@pytest.mark.offline
def test_unbound_ratchet_no_new_unbound() -> None:
    schema = create_app().openapi()
    assert_unbound_ratchet_no_new_unbound(schema, UNBOUND_BASELINE)


@pytest.mark.offline
def test_unbound_ratchet_baseline_not_stale() -> None:
    schema = create_app().openapi()
    assert_unbound_ratchet_baseline_not_stale(schema, UNBOUND_BASELINE)


@pytest.mark.offline
def test_committed_openapi_bindings_match_served() -> None:
    from openapi_mcp.binding import extract_typed_routes

    committed_path = _REPO_ROOT / "config" / "mcp" / "generated" / "cortex.openapi.json"
    committed = json.loads(committed_path.read_text(encoding="utf-8"))
    live = create_app().openapi()

    def binding_map(schema: dict) -> dict[str, tuple[str, str, str]]:
        routes = extract_typed_routes(schema)
        return {
            op: (route.method, route.path, route.operation_id)
            for op, route in routes.items()
        }

    assert binding_map(committed) == binding_map(live)


@pytest.mark.offline
def test_assert_served_bijection_falsifier_phantom_stamp() -> None:
    schema = deepcopy(create_app().openapi())
    for path, methods in schema.get("paths", {}).items():
        for method, spec in methods.items():
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue
            if isinstance(spec, dict) and "x-mcp" not in spec:
                spec["x-mcp"] = {"op": "phantom_op", "tool": "cortex"}
                with pytest.raises(AssertionError, match="phantom_op"):
                    assert_served_bijection(schema)
                return
    pytest.fail("no unstamped operation found for phantom falsifier")


@pytest.mark.offline
def test_assert_served_bijection_falsifier_operation_id_drift() -> None:
    schema = deepcopy(create_app().openapi())
    spec = schema["paths"]["/entities/{entity_id}"]["get"]
    spec["operationId"] = "mutated_entity_get_operation_id"
    with pytest.raises(AssertionError, match="entity_get"):
        assert_served_bijection(schema)


@pytest.mark.offline
def test_unbound_ratchet_reports_stamp_removal_as_new_unbound() -> None:
    schema = deepcopy(create_app().openapi())
    del schema["paths"]["/assertions"]["post"]["x-mcp"]
    new_unbound, _ = unbound_ratchet_violations(schema, UNBOUND_BASELINE)
    assert "assert" in new_unbound


@pytest.mark.offline
def test_reachable_unserved_violations_during_strangler() -> None:
    """Documents current strangler gap — live ops still lack typed routes."""
    live_ops = frozenset(
        {
            "assert",
            "assertion_update",
            "entity_update",
            "search",
        }
    )
    violations = find_reachable_unserved_violations(live_ops)
    assert "entity_get" not in violations
    assert "assert" not in violations


_ROUTING_HEADERS = frozenset(
    {
        "X-ULG-Surface",
        "X-ULG-Seat",
        "X-ULG-Caller",
        "X-ULG-Adapter",
        "X-ULG-Thread",
        "X-ULG-Session",
        "traceparent",
    }
)
_WRITE_METHODS = frozenset({"post", "put", "patch", "delete"})


@pytest.mark.offline
def test_every_stamped_op_declares_routing_headers() -> None:
    schema = create_app().openapi()
    seen = 0
    for path, methods in schema.get("paths", {}).items():
        if not isinstance(methods, dict):
            continue
        for method, spec in methods.items():
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue
            if not isinstance(spec, dict) or "x-mcp" not in spec:
                continue
            seen += 1
            params = [p for p in (spec.get("parameters") or []) if isinstance(p, dict)]
            headers = [p for p in params if p.get("in") == "header"]
            names = {p["name"] for p in headers}
            expected = set(_ROUTING_HEADERS)
            if method in _WRITE_METHODS:
                expected.add("Idempotency-Key")
            assert names == expected, f"{method.upper()} {path}: {sorted(names)}"
            assert len(headers) == len(names), f"duplicate headers on {method} {path}"
            for param in headers:
                assert param.get("required", False) is False
    assert seen > 0
