"""Generated MCP adapter manifest — do not edit by hand.

Regenerate:
  python scripts/openapi_mcp_codegen.py --write --service agent-bus
  python scripts/openapi_mcp_codegen.py --check --service agent-bus
"""

from __future__ import annotations

OPENAPI_SHA256 = "3eefef97798628d139f8d587c0831a771088cd2e0667184f5ac846c9bc7d0b3f"
FACADE_TOOL = "agent_bus"
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
