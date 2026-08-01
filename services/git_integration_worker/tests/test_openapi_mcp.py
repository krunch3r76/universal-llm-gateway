"""CI guards for git-integration-worker OpenAPI-first MCP adapter."""

from __future__ import annotations

import pytest
from openapi_mcp.binding import extract_typed_routes

from services.git_integration_worker.app import create_app
from services.git_integration_worker.openapi_mcp._ops import (
    DISPATCH_OP_CATALOG_TOOL,
    GIW_DISPATCH_OPS,
)
from services.git_integration_worker.openapi_mcp.codegen import (
    check_generated_module,
    dry_run_generate,
)

# trigger MCP tool op param — Literal["schedule", "list", "get", "cancel"] in tools/trigger.py
_TRIGGER_MCP_OPS: frozenset[str] = frozenset({"schedule", "list", "get", "cancel"})

_GIT_MCP_OPS: frozenset[str] = frozenset(
    {"integrate", "land", "status", "diff", "commit"}
)


@pytest.mark.offline
def test_giw_dispatch_ops_frozen_to_nine_ratified_ops() -> None:
    assert GIW_DISPATCH_OPS == _GIT_MCP_OPS | _TRIGGER_MCP_OPS


@pytest.mark.offline
def test_trigger_dispatch_ops_match_mcp_registration() -> None:
    assert _TRIGGER_MCP_OPS <= GIW_DISPATCH_OPS


@pytest.mark.offline
def test_extract_typed_routes_returns_nine_stamped_ops() -> None:
    schema = create_app().openapi()
    routes = extract_typed_routes(schema)
    assert len(routes) == 9
    assert set(routes) == set(GIW_DISPATCH_OPS)


@pytest.mark.offline
def test_unbound_dispatch_ops_returns_empty() -> None:
    from services.git_integration_worker.openapi_mcp._route_map import (
        unbound_dispatch_ops,
    )

    schema = create_app().openapi()
    assert unbound_dispatch_ops(schema) == []


@pytest.mark.offline
def test_stamped_ops_bindings_match_handler_direction() -> None:
    """Stamp-to-handler direction for all nine ops."""
    schema = create_app().openapi()
    routes = extract_typed_routes(schema)
    assert routes["integrate"].method == "POST"
    assert routes["integrate"].path == "/api/v1/git/integrate"
    assert routes["integrate"].tool == "git_integrate"
    assert routes["land"].method == "POST"
    assert routes["land"].path == "/api/v1/git/land"
    assert routes["land"].tool == "git_land"
    assert routes["commit"].method == "POST"
    assert routes["commit"].path == "/api/v1/git/commit"
    assert routes["commit"].tool == "git_commit"
    assert routes["status"].method == "GET"
    assert routes["status"].path == "/api/v1/git/status"
    assert routes["status"].tool == "git_status"
    assert routes["diff"].method == "GET"
    assert routes["diff"].path == "/api/v1/git/diff"
    assert routes["diff"].tool == "git_diff"
    assert routes["schedule"].method == "POST"
    assert routes["schedule"].path == "/api/v1/triggers"
    assert routes["schedule"].tool == "trigger"
    assert routes["cancel"].method == "DELETE"
    assert routes["cancel"].path == "/api/v1/triggers/{trigger_id}"
    assert routes["cancel"].tool == "trigger"


@pytest.mark.offline
def test_list_and_get_stamps_bind_distinct_trigger_routes() -> None:
    """GET /api/v1/triggers vs GET /api/v1/triggers/{trigger_id} must not swap."""
    schema = create_app().openapi()
    routes = extract_typed_routes(schema)
    list_route = routes["list"]
    get_route = routes["get"]
    assert list_route.method == "GET"
    assert list_route.path == "/api/v1/triggers"
    assert get_route.method == "GET"
    assert get_route.path == "/api/v1/triggers/{trigger_id}"
    assert list_route.tool == "trigger"
    assert get_route.tool == "trigger"
    assert (list_route.method, list_route.path) != (get_route.method, get_route.path)
    triggers = schema["paths"]["/api/v1/triggers"]
    assert triggers["get"]["x-mcp"]["op"] == "list"
    assert triggers["get"]["x-mcp"]["tool"] == "trigger"
    by_id = schema["paths"]["/api/v1/triggers/{trigger_id}"]
    assert by_id["get"]["x-mcp"]["op"] == "get"
    assert by_id["get"]["x-mcp"]["tool"] == "trigger"


@pytest.mark.offline
def test_catalog_tool_map_covers_every_dispatch_op() -> None:
    assert set(DISPATCH_OP_CATALOG_TOOL) == set(GIW_DISPATCH_OPS)


@pytest.mark.offline
def test_served_bindings_derived_from_native_route_stamps() -> None:
    schema = create_app().openapi()
    derived = extract_typed_routes(schema)
    manifest = dry_run_generate(schema)
    assert set(manifest.served_ops) == set(derived)


@pytest.mark.offline
def test_missing_stamp_is_detectable_not_silent() -> None:
    from copy import deepcopy

    from services.git_integration_worker.openapi_mcp._route_map import (
        unbound_dispatch_ops,
    )

    schema = deepcopy(create_app().openapi())
    assert "integrate" not in unbound_dispatch_ops(schema)
    assert check_generated_module(schema) is True

    del schema["paths"]["/api/v1/git/integrate"]["post"]["x-mcp"]

    assert "integrate" in unbound_dispatch_ops(schema)
    assert "integrate" not in dry_run_generate(schema).served_ops
    assert check_generated_module(schema) is False


@pytest.mark.offline
def test_generated_manifest_matches_openapi() -> None:
    schema = create_app().openapi()
    assert check_generated_module(schema) is True
