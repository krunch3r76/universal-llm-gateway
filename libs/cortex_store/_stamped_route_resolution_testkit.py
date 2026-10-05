"""Router-resolution probes for stamped substrate route shadow tests (S6 batch 3+)."""

from __future__ import annotations

from fastapi import FastAPI

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
)


def assert_baseline_route_resolution(app: FastAPI) -> None:
    """Named-route resolution via ``app.url_path_for`` (same graph as ``app.router``)."""
    for _method, route_name, path_params, _expected in ROUTE_RESOLUTION_PROBES:
        path = app.url_path_for(route_name, **path_params)
        assert path.startswith("/"), path


def resolve_endpoint_name(app: FastAPI, method: str, path: str) -> str | None:
    """Resolve batch-4 paths by reverse lookup on registered route names."""
    del method
    targets = {
        app.url_path_for("entities_bulk_upsert_route"): "entities_bulk_upsert_route",
        app.url_path_for("relationships_bulk_upsert_route"): "relationships_bulk_upsert_route",
    }
    return targets.get(path)
