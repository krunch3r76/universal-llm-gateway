"""Generated MCP adapter manifest — do not edit by hand.

Regenerate:
  python scripts/openapi_mcp_codegen.py --write
  python scripts/openapi_mcp_codegen.py --check
"""

from __future__ import annotations

OPENAPI_SHA256 = "4fe3923a2c9da07f21c2527ad54c8a2f14f5bfa1ccbeee843174581364ec10e7"
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
    "assertion_get": {
        "method": "GET",
        "path": "/assertions/{assertion_id}",
        "operation_id": "get_assertion_assertions__assertion_id__get",
    },
    "assertion_state": {
        "method": "GET",
        "path": "/entities/{entity_id}/assertion-state",
        "operation_id": "get_assertion_state_entities__entity_id__assertion_state_get",
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
    "entities_by_content_hash": {
        "method": "GET",
        "path": "/entities/by-content-hash/{content_hash}",
        "operation_id": "entities_by_content_hash_entities_by_content_hash__content_hash__get",
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
    "friction": {
        "method": "POST",
        "path": "/frictions",
        "operation_id": "create_friction_frictions_post",
    },
    "friction_close": {
        "method": "POST",
        "path": "/frictions/{assertion_id}/close",
        "operation_id": "close_friction_route_frictions__assertion_id__close_post",
    },
    "frictions": {
        "method": "GET",
        "path": "/frictions",
        "operation_id": "list_frictions_frictions_get",
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
    "observe": {
        "method": "POST",
        "path": "/assertions/observations",
        "operation_id": "observe_assertion_assertions_observations_post",
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
    "@components": "f2ad7f7835f1a812b9e83229fbdf874b4dc07c335eec3470c8f15fb03ba5445b",
    "@info": "1418971c74f9954e15f6a10ac814a7e27ff9889aca5f2d640c51dbeb6be527e4",
    "DELETE /tags/{tag_name}": "3fd67dfdaa2e5b50a1f1d192fa0e88e9722a19bc4dcdca19d12f3296c9be4c7f",
    "GET /api/v1/doctrine/vision-digest": "f161fc294c23139d04ed3d22bb992f730e4987e6b862e0d7258d278091a52dae",
    "GET /assertions/entrenchment": "9605a3ab98ba867b06bb5dd980a08a88e774d9e26dde79437bc6f1e49b6f962a",
    "GET /boot-commitments": "636723d0f74db39dcf9956c10f17833a479e3d3ed8a72acd9c85580a73892a97",
    "GET /boot-continuity": "724d463d4c52f0c57f1ce063b178fb920c593ff8aa571cf188e56c77120588b3",
    "GET /boot-gated": "3aa142fa3c7b0e57ad775d5582144e5d1b028d7622d9800fbc7f8ed8f33096c2",
    "GET /boot-legal-contacts": "153199ba0efbe9b48405c2afdfeb53f61085759f4ba96563e1a18cc67697bb17",
    "GET /boot-principal-context": "e5861b38f5ebdccba3009456eda65c4dddcc78a4138dd4b9a169366c5c9f2ebb",
    "GET /boot-recent-mentions": "98341cb64434b8d4a3f49ba39cd5022aa560ae4366f51082154ed26083268505",
    "GET /boot-recent-work": "05ba3d5cc04e3a92987574ea7199f951debb64694b74adb2b1539995ba3acd1f",
    "GET /boot-reflective": "478c1201008ebac981d81ab93cc76e4f2fe9fa9240d5b0079b7b0537e2592dd4",
    "GET /boot-sections": "ac8fbc7f001b87d9ba5011cd2b6e607d49d8d465e2bd4728bb226cb0c28509a6",
    "GET /boot-temporal": "d84bdbe4835a1be7d17c42194c58c891cda5c071e6ee41cdd6e0ea79b06900d5",
    "GET /boot-todos": "73c0eaef85db6dadb29772dc40aecc86343e710f431d5b8eb97314baa99b894b",
    "GET /control-tower": "4870431c2cf17cb1c11dd1ed3574ab64c4e1a4fb6ee3f71bd09c91129d29e051",
    "GET /control-tower/data": "636b7b6c318c6846f5aa0f40f5e77b6b136be4ace079614d856505dc7584124b",
    "GET /entities/source-paths": "0b8a46a498c611f4e6f03800f61433a97137c32e04322a6bf7abe86fec214937",
    "GET /entity-status/{entity_id}": "1650d15ebd156deaa97d804f053e77a62cd50515cbbe3689e2a874140bd6ff53",
    "GET /extraction-runs": "ffc2620ad7a69d00010c58fa7bacc7cf034563728eb85c46fbd2c08bb90ac320",
    "GET /health": "21989e2b6cca65135eca71d36eb223b0a748c249c8d88cdce6a4d8fdf1fca998",
    "GET /reaper/preview": "f610989a334525b989e0063c7cf917fd3f14989ae6388ae662f4a3aecb7351e0",
    "GET /salience": "013dfe36e4cbf8e883b7a23bb4399cedf4304d811d80010472d06061a666464a",
    "GET /skills": "9eb361655689256711ddb3332cd6c6bc95e745f0b4afc8a1ebc06f1595141706",
    "GET /skills/body": "f7dcc4cb0755ef9ee8b2e04e4dbf1e50d481d57d43905259b24e88c06cca70d9",
    "GET /staging/{staging_id}": "c822f68464d21c099707eb2d9efaebe0a68c0aacbe1a2c1cecc3f9e88cfc02cb",
    "GET /surface-forms/cache": "984b38883e22ae444909ef7fd86ef9f079618686579ccafa0bd4cd2fd124b61a",
    "PATCH /extraction-runs/{run_id}": "6ee470a6051f9db5986b2c696abf45ca1626f68e91848a7d853bbb423652c181",
    "POST /assertions/age-staged": "365d3e7c8a105134bb86d05abaa77fb2c828ea748ecdf3477aa8fdd0ecf05fbd",
    "POST /assertions/{assertion_id}/enrich": "327214561abfd1521908ccfaf5f39fd3481569f444f4460203d3674e2ad84c16",
    "POST /claims/burst": "fff7ee585ebe6616ae9e848182a5e3441f5748a982ee4dd95643d406178abf4d",
    "POST /close/check": "4152437ef6896ebf64db0dd27a5508dbad37e11d6ca4d184e01fb4f97221f8f2",
    "POST /close/commit": "d05802c27ec0f565be76fd8797a5bbd9dd0bbf7522a4aa0751dd26eb7901c1f1",
    "POST /close/draft": "5a5279e51c0c602ae70d6a162ee10354716d9fd08ed6425ec4aa42acdd32808e",
    "POST /close/handoff": "a7090be465751659d602b0677356fb47a9962f9bd510bec31bc03aa6fdc91c6d",
    "POST /close/stage": "b40e4aac7b9e85b75c2ea92277b80b37cacc6a3e3e144d9f54efdf89ef34d0ae",
    "POST /dispatch": "c74685d75274a8b2cc6d09d37def3eb11a2d8b0e8c0252ff0207749808700e1b",
    "POST /documents/ocr/directory": "73b65e04bb7660829b404be62064f10fe07da4f5bd372d1adbac3b5c2fbc2d8a",
    "POST /documents/ocr/file": "74b7099214c4e03f06e28290a75936c2328472f819aea21eeb67752e28263eac",
    "POST /extraction-runs/check": "3e85a328969c051d846146affe115b1091b1b6bba9c60acc9023fab6d2fe6bed",
    "POST /graph/imprint/commit": "1610bd0132f5e0d164085576a4368286de74be9e1e835f118ac4e9b20d282cb6",
    "POST /graph/imprint/propose": "35255f51d2714e4b5e125e368996159bde0c67a903fe428ca1f71424d3158911",
    "POST /graph/imprint/remember": "1e19c36eb00cba635d57f146c996b7f44a261368d57d56078059eeb2e838e827",
    "POST /graph/recall/continuity": "ba6f0998e06e42692fa8881da2d4997c6661ba9e6b5bfdd100fb6ec51f977583",
    "POST /graph/recall/matter": "d6017e519091c1b556bec55fb41aa40d9c89f37314730089d0f0e0d5925959d1",
    "POST /reaper/run": "97f9a9f6e59dbc84c71d5f783e30175c072b02ca7fbb97625768f1add086c387",
    "POST /session-journals": "1fbef5e73cd73470cf174393cfdea36c67ae04ed36bc8c6dc75983a66e626755",
    "POST /staging/batch": "6fb7e1c795e553032279cc3a17ba54ef7cec896a532516c1fa2a94642a516900",
    "POST /surface-forms": "0df54d1af78cc7d517e6074dc0c966123e4c7c6d101f608fb1c3061fe98ce06e",
}
