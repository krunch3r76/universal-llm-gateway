"""Router-resolution probes for stamped substrate route shadow tests (S6 batch 3+)."""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import FastAPI
from starlette.routing import Match, Mount, Route

# (HTTP method, FastAPI route name, path params, expected endpoint function name)
# at hub e6262356; batch 4 adds only entities/relationships bulk POST routes.
ROUTE_RESOLUTION_PROBES: tuple[tuple[str, str, dict[str, object], str], ...] = (
    ("GET", "get_assertion", {"assertion_id": 1}, "get_assertion"),
    ("GET", "search_assertions", {}, "search_assertions"),
    ("GET", "activate", {}, "activate"),
    ("POST", "observe_assertion", {}, "observe_assertion"),
    ("GET", "list_frictions", {}, "list_frictions"),
    ("POST", "create_friction", {}, "create_friction"),
    ("POST", "close_friction_route", {"assertion_id": 1}, "close_friction_route"),
    ("GET", "get_assertion_state", {"entity_id": "decision:probe"}, "get_assertion_state"),
    (
        "GET",
        "entities_by_content_hash",
        {"content_hash": "deadbeef"},
        "entities_by_content_hash",
    ),
    ("GET", "get_entity", {"entity_id": "decision:probe"}, "get_entity"),
    ("GET", "list_entities", {}, "list_entities"),
    ("GET", "list_entity_source_paths", {}, "list_entity_source_paths"),
    ("PATCH", "update_entity", {"entity_id": "decision:probe"}, "update_entity"),
    ("POST", "merge_entities", {}, "merge_entities"),
    ("POST", "rekey_entity", {"old_id": "decision:old"}, "rekey_entity"),
    ("POST", "create_entity", {}, "create_entity"),
    ("GET", "list_relationships", {}, "list_relationships"),
    ("POST", "create_relationship", {}, "create_relationship"),
    ("DELETE", "delete_relationship", {"relationship_id": 1}, "delete_relationship"),
    ("PATCH", "update_relationship", {"relationship_id": 1}, "update_relationship"),
    ("GET", "tag_resolve_route", {"tag_name": "current"}, "tag_resolve_route"),
    ("POST", "rj_consolidate_route", {}, "rj_consolidate_route"),
    (
        "POST",
        "deadline_resolve_route",
        {"deadline_id": "deadline:probe"},
        "deadline_resolve_route",
    ),
    ("POST", "entities_bulk_upsert_route", {}, "entities_bulk_upsert_route"),
    ("POST", "relationships_bulk_upsert_route", {}, "relationships_bulk_upsert_route"),
    ("POST", "recon_sidecar_write_route", {}, "recon_sidecar_write_route"),
    ("POST", "thread_sidecar_write_route", {}, "thread_sidecar_write_route"),
    ("POST", "todo_close_sidecar_route", {}, "todo_close_sidecar_route"),
    ("POST", "pinned_deliverable_write_route", {}, "pinned_deliverable_write_route"),
    ("POST", "endeavor_write_row_route", {}, "endeavor_write_row_route"),
    ("POST", "endeavor_dispose_row_route", {}, "endeavor_dispose_row_route"),
    (
        "POST",
        "view_render_route",
        {"document_id": "document:probe"},
        "view_render_route",
    ),
)


def _iter_api_routes(routes: list[object]) -> Iterator[Route]:
    """Yield Starlette ``Route`` objects from a FastAPI app (included routers included)."""
    for route in routes:
        original = getattr(route, "original_router", None)
        if original is not None:
            yield from _iter_api_routes(list(original.routes))
            continue
        if isinstance(route, Mount):
            yield from _iter_api_routes(route.routes)
        elif isinstance(route, Route):
            yield route
        elif hasattr(route, "routes"):
            yield from _iter_api_routes(list(route.routes))  # type: ignore[arg-type]


def resolve_endpoint_name(app: FastAPI, method: str, path: str) -> str | None:
    """First ``Route.matches`` FULL win for (method, path) — same order Starlette uses."""
    method_u = method.upper()
    scope = {
        "type": "http",
        "method": method.lower(),
        "path": path,
        "root_path": "",
        "headers": [],
    }
    for route in _iter_api_routes(list(app.router.routes)):
        if method_u not in route.methods:
            continue
        match, _child = route.matches(scope)
        if match in (Match.FULL, Match.PARTIAL):
            return route.endpoint.__name__
    return None


def assert_baseline_route_resolution(app: FastAPI) -> None:
    for method, route_name, path_params, expected in ROUTE_RESOLUTION_PROBES:
        path = app.url_path_for(route_name, **path_params)
        resolved = resolve_endpoint_name(app, method, path)
        assert resolved == expected, (
            f"{method} {path}: expected endpoint {expected!r}, got {resolved!r}"
        )
