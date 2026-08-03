"""Generated MCP adapter manifest — do not edit by hand.

Regenerate:
  python scripts/openapi_mcp_codegen.py --write --service giw
  python scripts/openapi_mcp_codegen.py --check --service giw
"""

from __future__ import annotations

OPENAPI_SHA256 = "622e2f7239f28ac7653dc024b7343cc9a90ba3177422f3ca02b347dbe35f6e2f"
FACADE_TOOL = "giw"
SERVED_OPS: dict[str, dict[str, str]] = {
    "cancel": {
        "method": "DELETE",
        "path": "/api/v1/triggers/{trigger_id}",
        "operation_id": "cancel_trigger_api_v1_triggers__trigger_id__delete",
        "tool": "trigger",
    },
    "commit": {
        "method": "POST",
        "path": "/api/v1/git/commit",
        "operation_id": "commit_api_v1_git_commit_post",
        "tool": "git_commit",
    },
    "diff": {
        "method": "GET",
        "path": "/api/v1/git/diff",
        "operation_id": "diff_api_v1_git_diff_get",
        "tool": "git_diff",
    },
    "get": {
        "method": "GET",
        "path": "/api/v1/triggers/{trigger_id}",
        "operation_id": "get_trigger_api_v1_triggers__trigger_id__get",
        "tool": "trigger",
    },
    "integrate": {
        "method": "POST",
        "path": "/api/v1/git/integrate",
        "operation_id": "integrate_api_v1_git_integrate_post",
        "tool": "git_integrate",
    },
    "land": {
        "method": "POST",
        "path": "/api/v1/git/land",
        "operation_id": "land_api_v1_git_land_post",
        "tool": "git_land",
    },
    "list": {
        "method": "GET",
        "path": "/api/v1/triggers",
        "operation_id": "list_triggers_api_v1_triggers_get",
        "tool": "trigger",
    },
    "schedule": {
        "method": "POST",
        "path": "/api/v1/triggers",
        "operation_id": "schedule_trigger_api_v1_triggers_post",
        "tool": "trigger",
    },
    "status": {
        "method": "GET",
        "path": "/api/v1/git/status",
        "operation_id": "status_api_v1_git_status_get",
        "tool": "git_status",
    },
}
NON_BINDING_PATH_FINGERPRINTS: dict[str, str] = {
    "@components": "b3682799f4b3fee61b16d4281b360bd49606f1b90cf25e1d4fb8fc30bd8f37a4",
    "@info": "ccfaa10f2c9b783c227fbd0286149ff177219e19207a5632fda5bd9fd68ce6d4",
    "GET /api/v1/cursor/catalog": "422c5cb1b562e05a1155b577da110a8c4563a5c55c9e8f9aaff2ff98a58b1990",
    "GET /api/v1/cursor/concurrency-stats": "83a8d8e0bcd07908c1338e4c11f1f7f59a41e332fb66d23a61a6adcbf75e2ff2",
    "GET /api/v1/git/active-work": "4d4a866e3c0dcff6b7982dffb3dfd3b5fa7a1cb5111683626fc5105a4403f7c6",
    "GET /api/v1/git/admin/dispatch-status": "7384a344be9e60ed3cb48acbfc6ae7833e588c9405e9be0c929e486a28e1864c",
    "GET /api/v1/git/admin/drain-state": "61b348f13c41e2159d14ac253069fc20ac3f8adc77fde502d0aac2cc9b7fba44",
    "GET /api/v1/git/admin/lease-snapshot": "ac1d5249fdbe3097061158331be0e60129f9a4aa6451ee470cf2351db6022ec4",
    "GET /api/v1/git/cursor-auto/liveness": "4ecedc90076065e2757dcd3b2212c68e1a3dbea024916aa262b90360e4f01934",
    "GET /api/v1/git/cursor-auto/queue": "05da0900e4cfa39ca0ee7e7ec879b0ce3dd95d63b492168be3c31ef748d83724",
    "GET /api/v1/git/reachable": "f8768ad0aaac86d697463c04d7be35375b2731cc218bfd555b350cb0bfb16c3d",
    "GET /health": "d26992eef4b192489b9414d3a2b6536d64db7097ece7f7f38c97df6629e30f65",
    "POST /api/v1/cursor/dispatch": "a09a8cbcf60307c6e0c65b560773f20dc5e33a80109bb2b110710b524685c9c9",
    "POST /api/v1/git/admin/begin-drain": "d485177474bfc0fa35078924cc201bad2bcd70b3f42db1b376d61e9e2b9e0bd3",
    "POST /api/v1/git/cursor-auto/enqueue": "e26a01c79ccbfd77c050f3d11a6a875d0afd4a2ca25b37e06613aec874f89dfd",
    "POST /api/v1/triggers/{trigger_id}/revoke": "9240662daadef7bc6b2255fbcab6755107ef7171d3a157f7ecab01ce3cfff76d",
}
