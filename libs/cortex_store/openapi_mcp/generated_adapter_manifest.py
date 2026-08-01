"""Generated MCP adapter manifest — do not edit by hand.

Regenerate:
  python scripts/openapi_mcp_codegen.py --write
  python scripts/openapi_mcp_codegen.py --check
"""

from __future__ import annotations

OPENAPI_SHA256 = "71078c6f2d79fb5e9679e836c8200f65dbf748fa173d1df236145084a88119ce"
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
    "assertion_update": {
        "method": "PATCH",
        "path": "/assertions/{assertion_id}",
        "operation_id": "update_assertion_assertions__assertion_id__patch",
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
    "edge_create": {
        "method": "POST",
        "path": "/edges",
        "operation_id": "create_edge_edges_post",
    },
    "edge_retire": {
        "method": "PATCH",
        "path": "/edges/{edge_id}/retire",
        "operation_id": "retire_edge_edges__edge_id__retire_patch",
    },
    "edge_traverse": {
        "method": "GET",
        "path": "/edges/traverse",
        "operation_id": "traverse_edges_traverse_get",
    },
    "edge_types": {
        "method": "GET",
        "path": "/edges/types",
        "operation_id": "list_edge_types_edges_types_get",
    },
    "edge_update": {
        "method": "PATCH",
        "path": "/edges/{edge_id}",
        "operation_id": "update_edge_edges__edge_id__patch",
    },
    "edges": {
        "method": "GET",
        "path": "/edges",
        "operation_id": "list_edges_edges_get",
    },
    "entities": {
        "method": "GET",
        "path": "/entities",
        "operation_id": "list_entities_entities_get",
    },
    "entity_create": {
        "method": "POST",
        "path": "/entities",
        "operation_id": "create_entity_entities_post",
    },
    "entity_get": {
        "method": "GET",
        "path": "/entities/{entity_id}",
        "operation_id": "get_entity_entities__entity_id__get",
    },
    "entity_merge": {
        "method": "POST",
        "path": "/entities/merge",
        "operation_id": "merge_entities_entities_merge_post",
    },
    "entity_rekey": {
        "method": "POST",
        "path": "/entities/{old_id}/rekey",
        "operation_id": "rekey_entity_entities__old_id__rekey_post",
    },
    "entity_update": {
        "method": "PATCH",
        "path": "/entities/{entity_id}",
        "operation_id": "update_entity_entities__entity_id__patch",
    },
    "impact": {
        "method": "GET",
        "path": "/edges/impact",
        "operation_id": "impact_analysis_edges_impact_get",
    },
    "journal_read": {
        "method": "GET",
        "path": "/session-journals",
        "operation_id": "list_session_journals_session_journals_get",
    },
    "relationship_create": {
        "method": "POST",
        "path": "/relationships",
        "operation_id": "create_relationship_relationships_post",
    },
    "relationship_delete": {
        "method": "DELETE",
        "path": "/relationships/{relationship_id}",
        "operation_id": "delete_relationship_relationships__relationship_id__delete",
    },
    "relationship_update": {
        "method": "PATCH",
        "path": "/relationships/{relationship_id}",
        "operation_id": "update_relationship_relationships__relationship_id__patch",
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
    "rj_link": {
        "method": "POST",
        "path": "/reflective-journal/{entry_id}/links",
        "operation_id": "add_link_reflective_journal__entry_id__links_post",
    },
    "rj_list": {
        "method": "GET",
        "path": "/reflective-journal",
        "operation_id": "list_entries_reflective_journal_get",
    },
    "rj_read": {
        "method": "GET",
        "path": "/reflective-journal/{entry_id}",
        "operation_id": "get_entry_reflective_journal__entry_id__get",
    },
    "rj_write": {
        "method": "POST",
        "path": "/reflective-journal",
        "operation_id": "create_entry_reflective_journal_post",
    },
    "search": {
        "method": "GET",
        "path": "/assertions/search",
        "operation_id": "search_assertions_assertions_search_get",
    },
    "session_close": {
        "method": "POST",
        "path": "/session-journals/close",
        "operation_id": "close_session_route_session_journals_close_post",
    },
    "session_handoff_upsert": {
        "method": "POST",
        "path": "/session-journals/{session_id}/handoff",
        "operation_id": "upsert_session_handoff_session_journals__session_id__handoff_post",
    },
    "staging_approve": {
        "method": "POST",
        "path": "/staging/{staging_id}/approve",
        "operation_id": "approve_staging_staging__staging_id__approve_post",
    },
    "staging_batch_approve": {
        "method": "POST",
        "path": "/staging/batch-approve",
        "operation_id": "approve_staging_batch_staging_batch_approve_post",
    },
    "staging_list": {
        "method": "GET",
        "path": "/staging",
        "operation_id": "list_staging_staging_get",
    },
    "staging_reject": {
        "method": "POST",
        "path": "/staging/{staging_id}/reject",
        "operation_id": "reject_staging_staging__staging_id__reject_post",
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
    "tag_assign": {
        "method": "PUT",
        "path": "/tags",
        "operation_id": "assign_tag_tags_put",
    },
    "tag_list": {
        "method": "GET",
        "path": "/tags",
        "operation_id": "list_tags_tags_get",
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
