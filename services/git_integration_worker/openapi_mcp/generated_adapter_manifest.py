"""Generated MCP adapter manifest — do not edit by hand.

Regenerate:
  python scripts/openapi_mcp_codegen.py --write --service giw
  python scripts/openapi_mcp_codegen.py --check --service giw
"""

from __future__ import annotations

OPENAPI_SHA256 = "a4c0b76b9d261fcdc0eb89dbdf15f71f6c3c8661d1315c8488123c93f74556db"
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
    "@components": "ce80823362811defaef61a26e01ed867a05986acdcb0f875567a2fc657b3832e",
    "@info": "ccfaa10f2c9b783c227fbd0286149ff177219e19207a5632fda5bd9fd68ce6d4",
    "DELETE /api/v1/cursor/dispatch/{dispatch_id}": "041e18df24dd1093ed38938e7d139a950c6716c59aba5ed4c07b3e7e50517332",
    "GET /api/v1/cursor/branch-debt": "9f86e8519040cbd44ae7dc934cacb730d82c8a56bb56e845bb8c0f124b24bf4e",
    "GET /api/v1/cursor/catalog": "5f3f9b6f213392c3f2e7ae16b9645f97cc8735522e52f536fb59ae4e82f8dbfd",
    "GET /api/v1/cursor/concurrency-stats": "e13e3bf99e50487deb17748a0b47925b1f9b9dd7091e803c8d7e5a3a6d0dcbdb",
    "GET /api/v1/git/active-work": "64ac6668067f8e0a05ff4f1da59bf2b6e5ad15b8df5ce62aca162497ee008d01",
    "GET /api/v1/git/admin/dispatch-status": "3faaefdaeb84ea5cbf6799432f23a4027108e21d5b0c95782bcd39a5bc9a5151",
    "GET /api/v1/git/admin/drain-state": "61b348f13c41e2159d14ac253069fc20ac3f8adc77fde502d0aac2cc9b7fba44",
    "GET /api/v1/git/admin/lease-snapshot": "ac1d5249fdbe3097061158331be0e60129f9a4aa6451ee470cf2351db6022ec4",
    "GET /api/v1/git/cursor-auto/job-state": "db4171d6debd8575dc1233ab3df051a46ce985996c94976085210af5bd4e7de1",
    "GET /api/v1/git/cursor-auto/liveness": "ee720a15ca65ebce8692a88c485e6e41f9ab8debb21ed34c75e7ed4b5f78858d",
    "GET /api/v1/git/cursor-auto/queue": "05da0900e4cfa39ca0ee7e7ec879b0ce3dd95d63b492168be3c31ef748d83724",
    "GET /api/v1/git/reachable": "f8768ad0aaac86d697463c04d7be35375b2731cc218bfd555b350cb0bfb16c3d",
    "GET /health": "5f4895dbe10f8bab0b97a798718236e7aa9bdf31b005093f009344ad42b23936",
    "POST /api/v1/cursor/branch-discharge": "1795f9ddd26b59147abc55390e10f80b464f914e59b0c04327facce713c30f9d",
    "POST /api/v1/cursor/dispatch": "a09a8cbcf60307c6e0c65b560773f20dc5e33a80109bb2b110710b524685c9c9",
    "POST /api/v1/cursor/dispatch/{dispatch_id}/park": "833754b851acb6b1232ed35371ae765541a06b5bf3cc72dc27c4174599dc62cb",
    "POST /api/v1/cursor/lane-worktree/release": "83917976539a00f911c9da30ec106021a977abc08d8adba6b7d4d4876415120e",
    "POST /api/v1/cursor/park-for-restart": "116a176ae0192ffa42a7748de0379cfa6e4e722e8842687c62d125ddbc093bac",
    "POST /api/v1/git/admin/begin-drain": "d485177474bfc0fa35078924cc201bad2bcd70b3f42db1b376d61e9e2b9e0bd3",
    "POST /api/v1/git/admin/cancel-drain": "ae603ef3f2fd2abe62a2412fe3a3fabb7adff561b2535b0da6467f400999bd96",
    "POST /api/v1/git/cursor-auto/enqueue": "e26a01c79ccbfd77c050f3d11a6a875d0afd4a2ca25b37e06613aec874f89dfd",
    "POST /api/v1/triggers/{trigger_id}/revoke": "9240662daadef7bc6b2255fbcab6755107ef7171d3a157f7ecab01ce3cfff76d",
}
