"""Dispatch-op → typed OpenAPI route bindings (served bucket)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Method = Literal["GET", "POST", "PUT", "PATCH", "DELETE"]


@dataclass(frozen=True, slots=True)
class TypedRoute:
    method: Method
    path: str
    operation_id: str


# Mechanical census from path-sim A §2.1 (2026-07-18 checkout).
TYPED_ROUTE_BY_OP: dict[str, TypedRoute] = {
    "activate": TypedRoute("GET", "/assertions/activate", "activate_assertions_activate_get"),
    "analyze_impact": TypedRoute(
        "POST",
        "/assertions/analyze-impact",
        "analyze_impact_semantic_assertions_analyze_impact_post",
    ),
    "assert": TypedRoute("POST", "/assertions", "create_assertion_assertions_post"),
    "assertions": TypedRoute("GET", "/assertions", "list_assertions_assertions_get"),
    "audit": TypedRoute(
        "GET", "/boot-audit-counters", "boot_audit_counters_boot_audit_counters_get"
    ),
    "deadlines": TypedRoute("GET", "/deadlines", "list_deadlines_deadlines_get"),
    "edge_types": TypedRoute("GET", "/edges/types", "list_edge_types_edges_types_get"),
    "edges": TypedRoute("POST", "/edges", "create_edge_edges_post"),
    "entities": TypedRoute("GET", "/entities", "list_entities_entities_get"),
    "impact": TypedRoute("GET", "/edges/impact", "impact_analysis_edges_impact_get"),
    "relationships": TypedRoute(
        "GET", "/relationships", "list_relationships_relationships_get"
    ),
    "render_subgraph": TypedRoute(
        "GET", "/subgraph/render", "render_subgraph_route_subgraph_render_get"
    ),
    "resolve": TypedRoute("GET", "/resolve", "resolve_cortex_uri_resolve_get"),
    "search": TypedRoute(
        "GET", "/assertions/search", "search_assertions_assertions_search_get"
    ),
    "stats": TypedRoute("GET", "/stats", "get_stats_stats_get"),
    "supersede": TypedRoute(
        "POST", "/assertions/supersede", "supersede_assertion_assertions_supersede_post"
    ),
    "surface_forms": TypedRoute(
        "GET", "/surface-forms", "list_surface_forms_surface_forms_get"
    ),
    "todo_audit": TypedRoute("GET", "/todo-audit", "get_todo_audit_todo_audit_get"),
    "todo_candidates": TypedRoute(
        "GET", "/todo-candidates", "get_todo_candidates_todo_candidates_get"
    ),
    "walk_subgraph": TypedRoute(
        "GET", "/subgraph/walk", "walk_subgraph_route_subgraph_walk_get"
    ),
}

# Adapter-orchestration ops — structurally untypeable on HTTP SOT (bucket d).
UNTYPEABLE_OPS: frozenset[str] = frozenset(
    {
        "doc_template",
        "doc_validate",
        "implement_ready_preflight",
        "resolve_assertion_chunk",
    }
)
