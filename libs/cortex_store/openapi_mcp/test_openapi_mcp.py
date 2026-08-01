"""CI guards for OpenAPI-first MCP adapter (OMDR-STRANGLER-S136)."""

from __future__ import annotations

import pytest

from cortex_store.dispatch_ops import _OP_SPECS
from cortex_store.main import create_app
from cortex_store.openapi_mcp.bijection import (
    assert_op_served_bijection,
    find_reachable_unserved_violations,
    served_operation_ids,
)
from cortex_store.openapi_mcp.census import build_four_bucket_census
from cortex_store.openapi_mcp.codegen import check_generated_module, dry_run_generate
from cortex_store.openapi_mcp.death_path import DEATH_PATH_GATE_DOC, death_path_gate_met
from cortex_store.openapi_mcp.schema_channel import SCHEMA_CHANNEL_DEFAULT


@pytest.mark.offline
def test_four_bucket_census_partitions_all_ops() -> None:
    census = build_four_bucket_census()
    assert census.total == len(_OP_SPECS)
    union = census.served | census.rb_only | census.neither | census.untypeable
    assert union == set(_OP_SPECS)


@pytest.mark.offline
def test_four_bucket_census_counts() -> None:
    census = build_four_bucket_census()
    assert len(census.served) == 45
    assert len(census.untypeable) == 4
    assert len(census.rb_only) == 20
    assert len(census.neither) == 15
    assert census.total == 84


@pytest.mark.offline
def test_assert_op_openapi_bijection() -> None:
    schema = create_app().openapi()
    assert_op_served_bijection(schema)


@pytest.mark.offline
def test_generator_dry_run_covers_served_ops() -> None:
    schema = create_app().openapi()
    manifest = dry_run_generate(schema)
    assert len(manifest.served_ops) == 45
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
def test_reachable_unserved_violations_during_strangler() -> None:
    """Documents current strangler gap — live ops still lack typed routes."""
    live_ops = frozenset(
        {
            "assertion_get",
            "assert",
            "assertion_update",
            "entity_update",
            "search",
        }
    )
    violations = find_reachable_unserved_violations(live_ops)
    assert "entity_get" not in violations
    assert "assert" not in violations
