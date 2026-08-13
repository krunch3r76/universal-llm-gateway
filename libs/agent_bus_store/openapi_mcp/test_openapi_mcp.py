"""CI guards for agent-bus OpenAPI-first MCP adapter."""

from __future__ import annotations

import pytest
from openapi_mcp.binding import extract_typed_routes

from agent_bus_store.openapi_mcp.codegen import check_generated_module, dry_run_generate
from agent_bus_store.server import create_app


@pytest.mark.offline
def test_extract_typed_routes_returns_nineteen_ops() -> None:
    schema = create_app().openapi()
    routes = extract_typed_routes(schema)
    assert len(routes) == 19
    assert "send" in routes
    assert "branch_associate" in routes
    assert "branch_current" in routes
    assert "request" not in routes
    assert "hop" not in routes
    assert "substrate_graph_write" not in routes
    assert "substrate_friction_file" not in routes
    assert "add_tags" not in routes
    assert "remove_tags" not in routes


@pytest.mark.offline
def test_served_bindings_derived_from_native_route_stamps() -> None:
    schema = create_app().openapi()
    derived = extract_typed_routes(schema)
    manifest = dry_run_generate(schema)
    assert set(manifest.served_ops) == set(derived)


@pytest.mark.offline
def test_unbound_ops_are_collision_losers_only() -> None:
    from agent_bus_store.openapi_mcp._route_map import unbound_dispatch_ops

    schema = create_app().openapi()
    unbound = unbound_dispatch_ops(schema)
    assert unbound == ["add_tags", "remove_tags"]
    assert "request" not in unbound
    assert "hop" not in unbound
    assert "substrate_graph_write" not in unbound
    assert "substrate_friction_file" not in unbound


@pytest.mark.offline
def test_missing_stamp_is_detectable_not_silent() -> None:
    from agent_bus_store.openapi_mcp._route_map import unbound_dispatch_ops

    schema = create_app().openapi()
    assert "send" not in unbound_dispatch_ops(schema)
    assert check_generated_module(schema) is True

    del schema["paths"]["/threads/send"]["post"]["x-mcp"]

    assert "send" in unbound_dispatch_ops(schema)
    assert "send" not in dry_run_generate(schema).served_ops
    assert check_generated_module(schema) is False


@pytest.mark.offline
def test_generated_manifest_matches_openapi() -> None:
    schema = create_app().openapi()
    assert check_generated_module(schema) is True


@pytest.mark.offline
def test_generator_dry_run_covers_served_ops() -> None:
    schema = create_app().openapi()
    manifest = dry_run_generate(schema)
    assert len(manifest.served_ops) == 19
    assert manifest.served_ops["send"]["path"] == "/threads/send"
    assert manifest.openapi_sha256
