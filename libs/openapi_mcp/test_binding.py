"""Hermetic tests for OpenAPI ``x-mcp`` binding derivation."""

from __future__ import annotations

import pytest

from openapi_mcp.binding import (
    TypedRoute,
    extract_typed_routes,
    inject_x_mcp,
)


def _sample_openapi() -> dict:
    return {
        "openapi": "3.1.0",
        "info": {"title": "sample", "version": "0"},
        "paths": {
            "/assertions": {
                "get": {
                    "operationId": "list_assertions_get",
                    "responses": {"200": {"description": "ok"}},
                },
                "post": {
                    "operationId": "create_assertion_post",
                    "responses": {"200": {"description": "ok"}},
                },
            },
            "/stats": {
                "get": {
                    "operationId": "get_stats_get",
                    "responses": {"200": {"description": "ok"}},
                },
            },
        },
    }


@pytest.mark.offline
def test_inject_then_extract_round_trip() -> None:
    seed = {
        "assertions": ("GET", "/assertions"),
        "assert": ("POST", "/assertions"),
        "stats": ("GET", "/stats"),
    }
    enriched = inject_x_mcp(
        _sample_openapi(),
        seed,
        tool="cortex",
        readonly_by_op={"assertions": True, "stats": True, "assert": False},
    )
    routes = extract_typed_routes(enriched)
    assert set(routes) == {"assertions", "assert", "stats"}
    assert routes["assert"] == TypedRoute(
        method="POST",
        path="/assertions",
        operation_id="create_assertion_post",
        tool="cortex",
        readonly=False,
    )
    assert routes["stats"].readonly is True


@pytest.mark.offline
def test_inject_raises_on_missing_path() -> None:
    with pytest.raises(RuntimeError, match="missing path"):
        inject_x_mcp(
            _sample_openapi(),
            {"ghost": ("GET", "/nope")},
            tool="cortex",
        )


@pytest.mark.offline
def test_extract_ignores_unbound_operations() -> None:
    assert extract_typed_routes(_sample_openapi()) == {}


@pytest.mark.offline
def test_extract_rejects_duplicate_ops() -> None:
    schema = _sample_openapi()
    schema["paths"]["/assertions"]["get"]["x-mcp"] = {
        "tool": "cortex",
        "op": "dup",
    }
    schema["paths"]["/stats"]["get"]["x-mcp"] = {"tool": "cortex", "op": "dup"}
    with pytest.raises(ValueError, match="duplicate"):
        extract_typed_routes(schema)


@pytest.mark.offline
def test_operation_id_comes_from_openapi_not_seed() -> None:
    """Seed carries only (method, path); operationId is live OpenAPI truth."""
    enriched = inject_x_mcp(
        _sample_openapi(),
        {"assert": ("POST", "/assertions")},
        tool="cortex",
    )
    assert (
        extract_typed_routes(enriched)["assert"].operation_id
        == "create_assertion_post"
    )
