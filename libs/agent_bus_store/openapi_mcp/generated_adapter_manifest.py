"""Generated MCP adapter manifest — do not edit by hand.

Regenerate:
  python scripts/openapi_mcp_codegen.py --write --service agent-bus
  python scripts/openapi_mcp_codegen.py --check --service agent-bus
"""

from __future__ import annotations

OPENAPI_SHA256 = "3eefef97798628d139f8d587c0831a771088cd2e0667184f5ac846c9bc7d0b3f"
FACADE_TOOL = "agent-bus"
SERVED_OPS: dict[str, dict[str, str]] = {
    "close": {
        "method": "PATCH",
        "path": "/threads/{thread_id}/close",
        "operation_id": "close_thread_route_threads__thread_id__close_patch",
    },
    "create_thread": {
        "method": "POST",
        "path": "/threads",
        "operation_id": "create_thread_route_threads_post",
    },
    "delete_thread": {
        "method": "DELETE",
        "path": "/threads/{thread_id}",
        "operation_id": "delete_thread_route_threads__thread_id__delete",
    },
    "delete_turn": {
        "method": "DELETE",
        "path": "/turns/{turn_id}",
        "operation_id": "delete_turn_route_turns__turn_id__delete",
    },
    "fetch": {
        "method": "GET",
        "path": "/turns",
        "operation_id": "list_turns_turns_get",
    },
    "fetch_unread": {
        "method": "GET",
        "path": "/turns/unread-toc",
        "operation_id": "list_unread_thread_toc_turns_unread_toc_get",
    },
    "get": {
        "method": "GET",
        "path": "/turns/by-number",
        "operation_id": "get_turn_by_number_route_turns_by_number_get",
    },
    "mark_read": {
        "method": "PATCH",
        "path": "/threads/{thread_id}/turns/read-state",
        "operation_id": "bulk_mark_read_state_route_threads__thread_id__turns_read_state_patch",
    },
    "post": {
        "method": "POST",
        "path": "/threads/with-turn",
        "operation_id": "create_thread_with_turn_route_threads_with_turn_post",
    },
    "reply": {
        "method": "POST",
        "path": "/turns",
        "operation_id": "create_turn_turns_post",
    },
    "send": {
        "method": "POST",
        "path": "/threads/send",
        "operation_id": "send_route_threads_send_post",
    },
    "thread_get": {
        "method": "GET",
        "path": "/threads/{thread_id}",
        "operation_id": "get_thread_route_threads__thread_id__get",
    },
    "threads": {
        "method": "GET",
        "path": "/threads",
        "operation_id": "list_threads_route_threads_get",
    },
    "triage": {
        "method": "POST",
        "path": "/threads/triage",
        "operation_id": "triage_threads_route_threads_triage_post",
    },
    "update": {
        "method": "PATCH",
        "path": "/turns/{turn_id}",
        "operation_id": "update_turn_route_turns__turn_id__patch",
    },
    "update_thread": {
        "method": "PATCH",
        "path": "/threads/{thread_id}",
        "operation_id": "update_thread_route_threads__thread_id__patch",
    },
    "wait": {
        "method": "GET",
        "path": "/threads/{thread_id}/wait",
        "operation_id": "wait_thread_route_threads__thread_id__wait_get",
    },
}
NON_BINDING_PATH_FINGERPRINTS: dict[str, str] = {
    "@components": "fa0b819d288b904eb1ee014c5e2d4f9626701081f59957e65a77061bcfc73183",
    "@info": "a8986fa23eba4ccbefb9d1d606b05ebcfa8474d790ceb9a8d83b4b3be5c8e983",
    "GET /dispatch-links/{execution_id}": "bec5bf28fc7e55d2aaae6fdd2f4af55a177f74f7030987e27c7bd9d73ebe86a3",
    "GET /health": "28f9baa09e00da33a5bfd7f4efb63b3cfd18fd05e7fe029a1871788e5629ad96",
    "GET /messages": "dfa9edc6b95121bb35b9ecca22260b564c40e4a8c4c180950101cb75619b0aa5",
    "GET /threads/{thread_id}/export": "a784f1abaed3c1f6839a38b2da63cff1f70cd335d3a9098fa3f2ebf10d8eb063",
    "GET /threads/{thread_id}/summary": "417a68f81d68bf2bc3d782fa38b0e437fe498480b1814bebddc34190be955f74",
    "PATCH /turns/{turn_id}/read": "d0c7fd2d62a6d60b88d62147bbe83cb63699716a9af07258f124e4a03794ad83",
    "PATCH /turns/{turn_id}/status": "b072109a07a08292a0b2047561d1162fd5be53eea6dd69e123d44f2ddf352d3f",
    "POST /messages": "8eb6200e7a4c6230aedbd411e976dff308399638011d2c871377267df36cdcb6",
    "POST /messages/{message_id}/read": "83761673ef954dc5a7199d0edad08a0e6efde98f66f8b0a65a0f6e7fe1a8257e",
    "POST /threads/{thread_id}/dispatch-admit": "27aad9c96e97de4adbe40143028f8c440509ac92d8aff28b7f21e75c86c2ed82",
    "POST /threads/{thread_id}/dispatch-claim-and-post": "4451b725f714794ab82d68c1654f4b46441122bd4ad9f6624853fa0e0b04002e",
    "POST /threads/{thread_id}/dispatch-terminate": "069bc1d52a990d3220dbaa715d3c2b5390450b7c3b3958feb9c6fdd9dfa6a5bb",
    "POST /threads/{thread_id}/rename": "b324915b8c6a73f4ce1d6baec4d71b6e0ec21e65a7e5fef3f217ad508540353d",
}
