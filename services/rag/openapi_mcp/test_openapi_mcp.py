"""CI guards for rag OpenAPI-first MCP adapter."""

from __future__ import annotations

import pytest
from openapi_mcp.binding import extract_typed_routes

from services.rag.openapi_mcp.codegen import check_generated_module, dry_run_generate
from services.rag.rag_service.main import app


@pytest.mark.offline
def test_extract_typed_routes_returns_six_stamped_ops() -> None:
    schema = app.openapi()
    routes = extract_typed_routes(schema)
    assert len(routes) == 6
    assert set(routes) == {
        "coverage",
        "upsert_article",
        "delete_source",
        "refresh_hints",
        "orphaned_articles",
        "delete_directory",
    }


@pytest.mark.offline
def test_served_bindings_derived_from_native_route_stamps() -> None:
    schema = app.openapi()
    derived = extract_typed_routes(schema)
    manifest = dry_run_generate(schema)
    assert set(manifest.served_ops) == set(derived)


@pytest.mark.offline
def test_unbound_dispatch_ops_returns_list_scopes_only() -> None:
    from services.rag.openapi_mcp._route_map import unbound_dispatch_ops

    schema = app.openapi()
    assert unbound_dispatch_ops(schema) == ["list_scopes"]


@pytest.mark.offline
def test_stamped_ops_bindings_match_handler_direction() -> None:
    """Stamped ops bind to the routes their handlers actually serve."""
    schema = app.openapi()
    routes = extract_typed_routes(schema)
    assert routes["coverage"].method == "GET"
    assert routes["coverage"].path == "/coverage"
    assert routes["upsert_article"].method == "POST"
    assert routes["upsert_article"].path == "/article"
    assert routes["delete_source"].method == "DELETE"
    assert routes["delete_source"].path == "/source"
    assert routes["refresh_hints"].method == "POST"
    assert routes["refresh_hints"].path == "/refresh_corpus_hints"
    assert routes["orphaned_articles"].method == "GET"
    assert routes["orphaned_articles"].path == "/orphaned_articles"
    assert routes["delete_directory"].method == "DELETE"
    assert routes["delete_directory"].path == "/directory"


@pytest.mark.offline
def test_search_is_not_bound_to_post_search() -> None:
    """The obvious wrong binding — search on POST /search — must stay absent."""
    schema = app.openapi()
    routes = extract_typed_routes(schema)
    assert "search" not in routes
    search_spec = schema["paths"]["/search"]["post"]
    assert "x-mcp" not in search_spec


@pytest.mark.offline
def test_missing_stamp_is_detectable_not_silent() -> None:
    from copy import deepcopy

    from services.rag.openapi_mcp._route_map import unbound_dispatch_ops

    schema = deepcopy(app.openapi())
    assert "coverage" not in unbound_dispatch_ops(schema)
    assert check_generated_module(schema) is True

    del schema["paths"]["/coverage"]["get"]["x-mcp"]

    assert "coverage" in unbound_dispatch_ops(schema)
    assert "coverage" not in dry_run_generate(schema).served_ops
    assert check_generated_module(schema) is False


@pytest.mark.offline
def test_generated_manifest_matches_openapi() -> None:
    schema = app.openapi()
    assert check_generated_module(schema) is True


@pytest.mark.offline
def test_untypeable_ops_exempt_from_unbound() -> None:
    from services.rag.openapi_mcp._route_map import UNTYPEABLE_OPS, unbound_dispatch_ops

    schema = app.openapi()
    unbound = unbound_dispatch_ops(schema)
    for op in UNTYPEABLE_OPS:
        assert op not in unbound
