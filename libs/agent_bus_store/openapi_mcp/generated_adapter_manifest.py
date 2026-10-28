"""Generated MCP adapter manifest — do not edit by hand.

Regenerate:
  python scripts/openapi_mcp_codegen.py --write --service agent-bus
  python scripts/openapi_mcp_codegen.py --check --service agent-bus
"""

from __future__ import annotations

OPENAPI_SHA256 = "5f06b55321ff09ad814bf91dfbcc472abf37ce3888f76c4cf7aef0f015098260"
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
    "delivery_bindings_resolve": {
        "method": "GET",
        "path": "/delivery-bindings/resolve",
        "operation_id": "delivery_bindings_resolve_route_delivery_bindings_resolve_get",
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
    "open_children": {
        "method": "GET",
        "path": "/threads/{thread_id}/open-children",
        "operation_id": "open_children_route_threads__thread_id__open_children_get",
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
    "subscribe": {
        "method": "POST",
        "path": "/endpoint-subscriptions",
        "operation_id": "subscribe_route_endpoint_subscriptions_post",
    },
    "subscriptions": {
        "method": "GET",
        "path": "/endpoint-subscriptions",
        "operation_id": "subscriptions_route_endpoint_subscriptions_get",
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
    "unsubscribe": {
        "method": "POST",
        "path": "/endpoint-subscriptions/unsubscribe",
        "operation_id": "unsubscribe_route_endpoint_subscriptions_unsubscribe_post",
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
    "@components": "eeb5c1f4b1ab0ba1482b80bec19c4e1e30747e74f870122d693a86021edd66ee",
    "@info": "a8986fa23eba4ccbefb9d1d606b05ebcfa8474d790ceb9a8d83b4b3be5c8e983",
    "GET /dispatch-links/{execution_id}": "bec5bf28fc7e55d2aaae6fdd2f4af55a177f74f7030987e27c7bd9d73ebe86a3",
    "GET /endpoint-wake/cursor": "2f6669d44f555a5e11f50ae4a1e39ee8dd234cb433d2173616b8c067d983d2c6",
    "GET /health": "1863eebbca661a08d0f2f879e48af294a3e3619e30ff42052a2ffdb33010d20c",
    "GET /house-actions": "3755f56f54751408e86380e1ad1de6a97930a066632ec5bfe0f4d598cd75fe08",
    "GET /messages": "dfa9edc6b95121bb35b9ecca22260b564c40e4a8c4c180950101cb75619b0aa5",
    "GET /resume-fences/{fence_id}": "faa8db7e13d971df64761d456de2b49efdfca13983139ff6d363a38e63671603",
    "GET /threads/{thread_id}/cse-current": "2f461416fdaf2260253067b60ddd42037cb95af90e91cea3726b23bff64c31d2",
    "GET /threads/{thread_id}/export": "a784f1abaed3c1f6839a38b2da63cff1f70cd335d3a9098fa3f2ebf10d8eb063",
    "GET /threads/{thread_id}/resume-fence": "9111adec1e5af49c6a2778f36ca005164511929b63dbe16cf93ea36e0344165c",
    "GET /threads/{thread_id}/summary": "417a68f81d68bf2bc3d782fa38b0e437fe498480b1814bebddc34190be955f74",
    "GET /turns/since": "93c587e9bae1612a749ddd982824e1fa1500da5e59554770b207dbec20659924",
    "PATCH /turns/{turn_id}/read": "fbc5092a653a58379b7b1a0317b69f9bdbaf31ff6bba8c539f7445af035e5a3b",
    "PATCH /turns/{turn_id}/status": "b072109a07a08292a0b2047561d1162fd5be53eea6dd69e123d44f2ddf352d3f",
    "POST /endpoint-wake/claim": "d2949781ebc9a2540d3da8fcc1823bf6d2b6f0577613bd4544c4e0dd697dfdee",
    "POST /endpoint-wake/result": "f1310a12b0da564f30d80686e279e0031eef1731f4406eabbc8a85afc2ce4dfc",
    "POST /endpoint-wake/scan": "53b666a90966c49959bbeac18f2896f65ba4fa2d410960ba513d43cf265f8453",
    "POST /house-actions": "c4059eb6e4641ef0e5efd5db87a7e758cc3ca6bd8af337caf09acb17ccd135d5",
    "POST /messages": "8eb6200e7a4c6230aedbd411e976dff308399638011d2c871377267df36cdcb6",
    "POST /messages/{message_id}/read": "83761673ef954dc5a7199d0edad08a0e6efde98f66f8b0a65a0f6e7fe1a8257e",
    "POST /resume-fences/{fence_id}/denied": "dc85bdb04fa8d904fbca7730155eaf58384e998d5f73fa12401b772395d2cda7",
    "POST /resume-fences/{fence_id}/expire": "d9276eeae3a53e54f7dc42ed20b6ad688e0bfd4980f8f83ed4f6b874e90ef669",
    "POST /resume-fences/{fence_id}/release": "b463ab4100730d82c76dfa638508dbd703991c128faec0266fbbba884e323204",
    "POST /threads/{thread_id}/cse-associate": "53b29011b1b08907286ffff3e9a3f7907ba3cd643bd1ac9f032521193ce01d5a",
    "POST /threads/{thread_id}/dispatch-admit": "27aad9c96e97de4adbe40143028f8c440509ac92d8aff28b7f21e75c86c2ed82",
    "POST /threads/{thread_id}/dispatch-admit-and-post": "cee9ef0730afeff1baa883e4be65414ce54de11ba36acd1ac76ab83ce750f389",
    "POST /threads/{thread_id}/dispatch-claim-and-post": "6debd6c60e85ee2f80877d84e66f884583e53b67c87fc8cfb8fe96961837b6dd",
    "POST /threads/{thread_id}/dispatch-terminate": "69e03b187a84e46cbbd0057a9efeae059892b6fcb6ab176bd53322ca37c87da5",
    "POST /threads/{thread_id}/rename": "b324915b8c6a73f4ce1d6baec4d71b6e0ec21e65a7e5fef3f217ad508540353d",
    "POST /threads/{thread_id}/resume-fence/arm": "59cf0c063d4f918c72af8f0ed45ffa6688cba2f84d82400ffa1086e8f7d9842a",
}
