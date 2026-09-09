"""Generated MCP adapter manifest — do not edit by hand.

Regenerate:
  python scripts/openapi_mcp_codegen.py --write --service agent-bus
  python scripts/openapi_mcp_codegen.py --check --service agent-bus
"""

from __future__ import annotations

OPENAPI_SHA256 = "17c5706b9df09675475ba873f3eeaa45239c5e7ce2c9f7e92286547cc66a8a16"
FACADE_TOOL = "agent-bus"
SERVED_OPS: dict[str, dict[str, str]] = {
    "branch_associate": {
        "method": "POST",
        "path": "/threads/{thread_id}/branch-associate",
        "operation_id": "branch_associate_route_threads__thread_id__branch_associate_post",
    },
    "branch_current": {
        "method": "GET",
        "path": "/threads/{thread_id}/branch-current",
        "operation_id": "branch_current_route_threads__thread_id__branch_current_get",
    },
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
    "lane_bind": {
        "method": "POST",
        "path": "/threads/{thread_id}/lane-bind",
        "operation_id": "lane_bind_route_threads__thread_id__lane_bind_post",
    },
    "lane_current": {
        "method": "GET",
        "path": "/threads/{thread_id}/lane-current",
        "operation_id": "lane_current_route_threads__thread_id__lane_current_get",
    },
    "lineage": {
        "method": "GET",
        "path": "/threads/{thread_id}/lineage",
        "operation_id": "thread_lineage_route_threads__thread_id__lineage_get",
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
    "resume_fence": {
        "method": "POST",
        "path": "/threads/{thread_id}/resume-fence",
        "operation_id": "create_resume_fence_threads__thread_id__resume_fence_post",
    },
    "send": {
        "method": "POST",
        "path": "/threads/send",
        "operation_id": "send_route_threads_send_post",
    },
    "tape": {
        "method": "GET",
        "path": "/threads/{thread_id}/tape",
        "operation_id": "tape_route_threads__thread_id__tape_get",
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
    "@components": "4251b8134c08f948b5d0275c3e9cbf4aa4ccda746d799c747aab0736ef263026",
    "@info": "a8986fa23eba4ccbefb9d1d606b05ebcfa8474d790ceb9a8d83b4b3be5c8e983",
    "GET /dispatch-links/{execution_id}": "bec5bf28fc7e55d2aaae6fdd2f4af55a177f74f7030987e27c7bd9d73ebe86a3",
    "GET /health": "1863eebbca661a08d0f2f879e48af294a3e3619e30ff42052a2ffdb33010d20c",
    "GET /messages": "dfa9edc6b95121bb35b9ecca22260b564c40e4a8c4c180950101cb75619b0aa5",
    "GET /resume-fences/{fence_id}": "faa8db7e13d971df64761d456de2b49efdfca13983139ff6d363a38e63671603",
    "GET /threads/{thread_id}/export": "a784f1abaed3c1f6839a38b2da63cff1f70cd335d3a9098fa3f2ebf10d8eb063",
    "GET /threads/{thread_id}/summary": "417a68f81d68bf2bc3d782fa38b0e437fe498480b1814bebddc34190be955f74",
    "PATCH /turns/{turn_id}/read": "d0c7fd2d62a6d60b88d62147bbe83cb63699716a9af07258f124e4a03794ad83",
    "PATCH /turns/{turn_id}/status": "b072109a07a08292a0b2047561d1162fd5be53eea6dd69e123d44f2ddf352d3f",
    "POST /messages": "8eb6200e7a4c6230aedbd411e976dff308399638011d2c871377267df36cdcb6",
    "POST /messages/{message_id}/read": "83761673ef954dc5a7199d0edad08a0e6efde98f66f8b0a65a0f6e7fe1a8257e",
    "POST /resume-fences/{fence_id}/denied": "dc85bdb04fa8d904fbca7730155eaf58384e998d5f73fa12401b772395d2cda7",
    "POST /resume-fences/{fence_id}/release": "b463ab4100730d82c76dfa638508dbd703991c128faec0266fbbba884e323204",
    "POST /threads/{thread_id}/dispatch-admit": "27aad9c96e97de4adbe40143028f8c440509ac92d8aff28b7f21e75c86c2ed82",
    "POST /threads/{thread_id}/dispatch-claim-and-post": "4451b725f714794ab82d68c1654f4b46441122bd4ad9f6624853fa0e0b04002e",
    "POST /threads/{thread_id}/dispatch-terminate": "069bc1d52a990d3220dbaa715d3c2b5390450b7c3b3958feb9c6fdd9dfa6a5bb",
    "POST /threads/{thread_id}/rename": "b324915b8c6a73f4ce1d6baec4d71b6e0ec21e65a7e5fef3f217ad508540353d",
}
