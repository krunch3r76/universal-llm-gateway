"""Generated MCP adapter manifest — do not edit by hand.

Regenerate:
  python scripts/openapi_mcp_codegen.py --write
  python scripts/openapi_mcp_codegen.py --check
"""

from __future__ import annotations

OPENAPI_SHA256 = "a77c09f986b8a119e293602ec4d35ac2b50c8e0947bf86d1683daccfdd206212"
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
    "seat_claim": {
        "method": "POST",
        "path": "/seat-claims/claim",
        "operation_id": "seat_claim_route_seat_claims_claim_post",
    },
    "seat_claims_list": {
        "method": "GET",
        "path": "/seat-claims",
        "operation_id": "seat_claims_list_route_seat_claims_get",
    },
    "seat_heartbeat": {
        "method": "POST",
        "path": "/seat-claims/heartbeat",
        "operation_id": "seat_heartbeat_route_seat_claims_heartbeat_post",
    },
    "seat_release": {
        "method": "POST",
        "path": "/seat-claims/release",
        "operation_id": "seat_release_route_seat_claims_release_post",
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
NON_BINDING_PATH_FINGERPRINTS: dict[str, str] = {
    "@components": "ad2f9be75bbf9dcd58055c30943dd27829918be0b191d491d4de5999912588f2",
    "@info": "1418971c74f9954e15f6a10ac814a7e27ff9889aca5f2d640c51dbeb6be527e4",
    "DELETE /tags/{tag_name}": "9fbd9e2ed8d290a1c189d7b3e47d6c8afcfddd1d9dbdf534d33e95f1fc39fb59",
    "GET /api/v1/doctrine/vision-digest": "f161fc294c23139d04ed3d22bb992f730e4987e6b862e0d7258d278091a52dae",
    "GET /assertions/entrenchment": "ebe5d5c87a61da19fca1e3429e3c7c7d57b3261c216ae73696d2a2115b064f6e",
    "GET /boot-commitments": "b4f8f9ba7f469bbd89080e7525150b567d2b0dc50e80456918c1e4506ffb4bc9",
    "GET /boot-continuity": "46826560418175d99c7b3dd9a9fb5c41c6f528f44989d91bf1ce7e8980d1fbdf",
    "GET /boot-gated": "3aa142fa3c7b0e57ad775d5582144e5d1b028d7622d9800fbc7f8ed8f33096c2",
    "GET /boot-legal-contacts": "7b7344e1e3ef12b5b6517d19fd6571fe57725d0a48ff0b038ce7533b784e6bb1",
    "GET /boot-principal-context": "8b286d3c009dfbc2d09c0ca21c7a1bc5efb2acfefa5014b8091f55f6cf3e5033",
    "GET /boot-recent-mentions": "a74f53b425bde854dc64f07962dbda0dac7f818aae9bed566a8c8a578c144ce4",
    "GET /boot-recent-work": "b8679b32afbf7ff7f6abba6212e67244567b81757c9e9bb2014756a828cc8f27",
    "GET /boot-reflective": "abdca3c780a13b0f0a59496cc7882f053e9a644480215d5992e7fe8dec4d161c",
    "GET /boot-sections": "e7591dc21d07fd51d50eebe40c85361d2f5d479879d6ad99b206ea9a4c7dc4f4",
    "GET /boot-temporal": "21ee716b5fc4e08c210ec84ace1d78ea0bd16d9b82a701bc2b5b9fba4e4ee049",
    "GET /boot-todos": "7d8d1f541af882068ec2c5c7a6696e65bf23a097bc9a77b97c86ff7ebb31f32d",
    "GET /control-tower": "128b4b9de7f880971e3cc333967abc77d0729635f8f8ef6dd2106d75cd77bba1",
    "GET /control-tower/data": "26a8708c8b2e625e12fce2afafbac7408b51ab6c1c85458b82b675a8ad030bb1",
    "GET /entities/source-paths": "e67cc7bdb6c3e0485521102ad543dc0e6897cb5df65440f04a4f169c90fb9e94",
    "GET /entity-status/{entity_id}": "1650d15ebd156deaa97d804f053e77a62cd50515cbbe3689e2a874140bd6ff53",
    "GET /extraction-runs": "ffc2620ad7a69d00010c58fa7bacc7cf034563728eb85c46fbd2c08bb90ac320",
    "GET /health": "21989e2b6cca65135eca71d36eb223b0a748c249c8d88cdce6a4d8fdf1fca998",
    "GET /reaper/preview": "f610989a334525b989e0063c7cf917fd3f14989ae6388ae662f4a3aecb7351e0",
    "GET /salience": "013dfe36e4cbf8e883b7a23bb4399cedf4304d811d80010472d06061a666464a",
    "GET /skills": "9eb361655689256711ddb3332cd6c6bc95e745f0b4afc8a1ebc06f1595141706",
    "GET /skills/body": "f7dcc4cb0755ef9ee8b2e04e4dbf1e50d481d57d43905259b24e88c06cca70d9",
    "GET /staging/{staging_id}": "874fbc8a98045b8af67d7064770ea33e1c36158725428a664cb74b1e02f38948",
    "GET /surface-forms/cache": "67218e356776ecb6899d4130b0446d0f1a207ddd0f6a66541a1a5a35623387cc",
    "PATCH /extraction-runs/{run_id}": "6ee470a6051f9db5986b2c696abf45ca1626f68e91848a7d853bbb423652c181",
    "POST /assertions/age-staged": "365d3e7c8a105134bb86d05abaa77fb2c828ea748ecdf3477aa8fdd0ecf05fbd",
    "POST /assertions/{assertion_id}/enrich": "b5d3745696d9a200a3bc7c7ca5c07d18f64a790b9e9688227ba91951e66cc42a",
    "POST /claims/burst": "fff7ee585ebe6616ae9e848182a5e3441f5748a982ee4dd95643d406178abf4d",
    "POST /close/check": "4152437ef6896ebf64db0dd27a5508dbad37e11d6ca4d184e01fb4f97221f8f2",
    "POST /close/commit": "d05802c27ec0f565be76fd8797a5bbd9dd0bbf7522a4aa0751dd26eb7901c1f1",
    "POST /close/draft": "5a5279e51c0c602ae70d6a162ee10354716d9fd08ed6425ec4aa42acdd32808e",
    "POST /close/handoff": "a7090be465751659d602b0677356fb47a9962f9bd510bec31bc03aa6fdc91c6d",
    "POST /close/stage": "b40e4aac7b9e85b75c2ea92277b80b37cacc6a3e3e144d9f54efdf89ef34d0ae",
    "POST /dispatch": "f827d8807e42d5031210f579d66d74662c1ef8ddca26589242b605db6ad7c4d0",
    "POST /documents/ocr/directory": "73b65e04bb7660829b404be62064f10fe07da4f5bd372d1adbac3b5c2fbc2d8a",
    "POST /documents/ocr/file": "74b7099214c4e03f06e28290a75936c2328472f819aea21eeb67752e28263eac",
    "POST /extraction-runs/check": "3e85a328969c051d846146affe115b1091b1b6bba9c60acc9023fab6d2fe6bed",
    "POST /graph/imprint/commit": "1610bd0132f5e0d164085576a4368286de74be9e1e835f118ac4e9b20d282cb6",
    "POST /graph/imprint/propose": "35255f51d2714e4b5e125e368996159bde0c67a903fe428ca1f71424d3158911",
    "POST /graph/imprint/remember": "1e19c36eb00cba635d57f146c996b7f44a261368d57d56078059eeb2e838e827",
    "POST /reaper/run": "97f9a9f6e59dbc84c71d5f783e30175c072b02ca7fbb97625768f1add086c387",
    "POST /session-journals": "b31c15f8382d52d935fb24f61eeca8de9c1b0687e7d30b731ae33ba6a0a33157",
    "POST /staging/batch": "7794640d5f623f147361212684347934c0ec0b7b09427871ce4685a80ee1bf9a",
    "POST /surface-forms": "d14546bcbd84adc2097529dda1d41769f3a998281d6793d0ecf1109859f2206e",
}
