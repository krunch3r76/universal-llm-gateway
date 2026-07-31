"""Generated MCP adapter manifest — do not edit by hand.

Regenerate:
  python scripts/openapi_mcp_codegen.py --write
  python scripts/openapi_mcp_codegen.py --check
"""

from __future__ import annotations

OPENAPI_SHA256 = "b14d1b9a841e5f91f4f8e1ec00914dd328a89ba2c138ebad84f6d2b5763edac9"
FACADE_TOOL = "cortex"
SERVED_OPS: dict[str, dict[str, str]] = {
    "activate": {
        "method": "GET",
        "path": "/assertions/activate",
        "operation_id": "activate_assertions_activate_get",
    },
    "analyze_impact": {
        "method": "POST",
        "path": "/assertions/analyze-impact",
        "operation_id": "analyze_impact_semantic_assertions_analyze_impact_post",
    },
    "assert": {
        "method": "POST",
        "path": "/assertions",
        "operation_id": "create_assertion_assertions_post",
    },
    "assertions": {
        "method": "GET",
        "path": "/assertions",
        "operation_id": "list_assertions_assertions_get",
    },
    "audit": {
        "method": "GET",
        "path": "/boot-audit-counters",
        "operation_id": "boot_audit_counters_boot_audit_counters_get",
    },
    "deadlines": {
        "method": "GET",
        "path": "/deadlines",
        "operation_id": "list_deadlines_deadlines_get",
    },
    "edge_types": {
        "method": "GET",
        "path": "/edges/types",
        "operation_id": "list_edge_types_edges_types_get",
    },
    "edges": {
        "method": "POST",
        "path": "/edges",
        "operation_id": "create_edge_edges_post",
    },
    "entities": {
        "method": "GET",
        "path": "/entities",
        "operation_id": "list_entities_entities_get",
    },
    "impact": {
        "method": "GET",
        "path": "/edges/impact",
        "operation_id": "impact_analysis_edges_impact_get",
    },
    "relationships": {
        "method": "GET",
        "path": "/relationships",
        "operation_id": "list_relationships_relationships_get",
    },
    "render_subgraph": {
        "method": "GET",
        "path": "/subgraph/render",
        "operation_id": "render_subgraph_route_subgraph_render_get",
    },
    "resolve": {
        "method": "GET",
        "path": "/resolve",
        "operation_id": "resolve_cortex_uri_resolve_get",
    },
    "search": {
        "method": "GET",
        "path": "/assertions/search",
        "operation_id": "search_assertions_assertions_search_get",
    },
    "stats": {
        "method": "GET",
        "path": "/stats",
        "operation_id": "get_stats_stats_get",
    },
    "supersede": {
        "method": "POST",
        "path": "/assertions/supersede",
        "operation_id": "supersede_assertion_assertions_supersede_post",
    },
    "surface_forms": {
        "method": "GET",
        "path": "/surface-forms",
        "operation_id": "list_surface_forms_surface_forms_get",
    },
    "todo_audit": {
        "method": "GET",
        "path": "/todo-audit",
        "operation_id": "get_todo_audit_todo_audit_get",
    },
    "todo_candidates": {
        "method": "GET",
        "path": "/todo-candidates",
        "operation_id": "get_todo_candidates_todo_candidates_get",
    },
    "walk_subgraph": {
        "method": "GET",
        "path": "/subgraph/walk",
        "operation_id": "walk_subgraph_route_subgraph_walk_get",
    },
}
