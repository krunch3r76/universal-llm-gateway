"""Router-resolution probes for stamped substrate route shadow tests (S6 batch 3+)."""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

from fastapi import FastAPI
from starlette.routing import Match, Mount, Route, compile_path

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
        "register_skill_substrate_route",
        {},
        "register_skill_substrate_route",
    ),
)

_FASTAPI_PATH_PARAM_RE = re.compile(r"\{([^}:]+)(?::[^}]+)?\}")


def _concrete_path(route_path: str, param_convertors: dict[str, Any]) -> str:
    def _repl(match: re.Match[str]) -> str:
        key = match.group(1)
        sample = _PATH_PARAM_SAMPLES.get(key)
        if sample is None:
            convertor = param_convertors.get(key)
            if convertor and convertor.__class__.__name__ == "IntegerConverter":
                sample = 1
            else:
                sample = f"probe-{key}"
        return str(sample)

    return _FASTAPI_PATH_PARAM_RE.sub(_repl, route_path)


_PATH_PARAM_SAMPLES: dict[str, Any] = {
    "assertion_id": 1,
    "entity_id": "decision:ac4-probe",
    "content_hash": "deadbeef",
    "tag_name": "current",
    "deadline_id": "deadline:probe",
    "relationship_id": 1,
    "old_id": "decision:old",
    "document_id": "document:probe-view",
}


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
        "method": method_u,
        "path": path,
        "root_path": "",
        "headers": [],
    }
    for route in _iter_api_routes(list(app.router.routes)):
        if method_u not in route.methods:
            continue
        match, _child = route.matches(scope)
        if match == Match.FULL:
            return route.endpoint.__name__
    return None


_BATCH6_NEW_ENDPOINTS = frozenset(
    {"endeavor_write_row_route", "endeavor_dispose_row_route"}
)

_BATCH7_NEW_ENDPOINTS = frozenset({"register_skill_substrate_route"})


def iter_app_route_probes(app: FastAPI) -> Iterator[tuple[str, str, str]]:
    """Every named Starlette route → (method, concrete path, endpoint __name__)."""
    for route in _iter_api_routes(list(app.router.routes)):
        if not isinstance(route, Route) or not route.name:
            continue
        _, _path_fmt, param_convertors = compile_path(route.path)
        path = _concrete_path(route.path, param_convertors)
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            yield method, path, route.endpoint.__name__


def assert_every_route_forward_matches_self(
    app: FastAPI,
    *,
    skip_endpoint_names: frozenset[str] = frozenset(),
) -> None:
    for method, path, expected in iter_app_route_probes(app):
        if expected in skip_endpoint_names:
            continue
        resolved = resolve_endpoint_name(app, method, path)
        assert resolved == expected, (
            f"{method} {path}: expected endpoint {expected!r}, got {resolved!r}"
        )


def assert_baseline_route_resolution(app: FastAPI) -> None:
    for method, route_name, path_params, expected in ROUTE_RESOLUTION_PROBES:
        path = app.url_path_for(route_name, **path_params)
        resolved = resolve_endpoint_name(app, method, path)
        assert resolved == expected, (
            f"{method} {path}: expected endpoint {expected!r}, got {resolved!r}"
        )


def assert_pre_batch6_route_resolution_unchanged(app: FastAPI) -> None:
    """AC4: every pre-batch-6 stamped route still resolves to its own endpoint (FULL match)."""
    for method, route_name, path_params, expected in ROUTE_RESOLUTION_PROBES:
        if expected in _BATCH6_NEW_ENDPOINTS:
            continue
        path = app.url_path_for(route_name, **path_params)
        resolved = resolve_endpoint_name(app, method, path)
        assert resolved == expected, (
            f"{method} {path}: expected endpoint {expected!r}, got {resolved!r}"
        )


def assert_pre_batch7_route_resolution_unchanged(app: FastAPI) -> None:
    """AC4: every served route at batch-6 tip still FULL-matches its endpoint (batch 6–7 new excluded)."""
    assert_every_route_forward_matches_self(
        app,
        skip_endpoint_names=_BATCH6_NEW_ENDPOINTS | _BATCH7_NEW_ENDPOINTS,
    )
