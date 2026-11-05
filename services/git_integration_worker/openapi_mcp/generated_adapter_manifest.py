"""Generated MCP adapter manifest — do not edit by hand.

Regenerate:
  python scripts/openapi_mcp_codegen.py --write --service giw
  python scripts/openapi_mcp_codegen.py --check --service giw
"""

from __future__ import annotations

OPENAPI_SHA256 = "6e75ec8d9c68df392e495e5cde2cd413e8aa44f390cb416218060a5b0cdaa2f4"
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
    "@components": "dbcf35ea4450a603eabbf9cef474f856bd3883b53918ae86d86aaa9c1c86efbc",
    "@info": "ccfaa10f2c9b783c227fbd0286149ff177219e19207a5632fda5bd9fd68ce6d4",
    "DELETE /api/v1/cursor/dispatch/{dispatch_id}": "041e18df24dd1093ed38938e7d139a950c6716c59aba5ed4c07b3e7e50517332",
    "GET /api/v1/cursor/branch-debt": "9f86e8519040cbd44ae7dc934cacb730d82c8a56bb56e845bb8c0f124b24bf4e",
    "GET /api/v1/cursor/catalog": "5f3f9b6f213392c3f2e7ae16b9645f97cc8735522e52f536fb59ae4e82f8dbfd",
    "GET /api/v1/cursor/concurrency-stats": "e13e3bf99e50487deb17748a0b47925b1f9b9dd7091e803c8d7e5a3a6d0dcbdb",
    "GET /api/v1/cursor/dispatch-usage-live": "f16f2830ea283aa0e6597c74502ebc52e81a13c2e44f38fec37960d46d0e77ad",
    "GET /api/v1/cursor/dispatch/latest-terminal-conductor": "b17f4825df513f872a65fabb126f53d3c9116412d384ea64b367201e4e35fa71",
    "GET /api/v1/cursor/dispatch/{dispatch_id}/conversation": "ae6558d446f3e8059d938631b37d899f869db58b6ca0eb1e281a516406271849",
    "GET /api/v1/cursor/occupancy": "39f82ffdc134246107496a832f84fffc505e5585479e90a0cfa5729ca8698389",
    "GET /api/v1/cursor/registry": "ced6b72e145561ab5031e2e896da9745c6a349e3087733f49f93eab9cec8a6da",
    "GET /api/v1/cursor/work-key-ledger": "c0ea2d7abe57afe322bc6edd6166b624ae9e50b41f94b5295e63ccdcfd0559b4",
    "GET /api/v1/git/active-work": "64ac6668067f8e0a05ff4f1da59bf2b6e5ad15b8df5ce62aca162497ee008d01",
    "GET /api/v1/git/admin/cse-holders": "c3dafb745a27b9d44990e3b49f78ba70ffb39def51266ea20ded4b03c4acad22",
    "GET /api/v1/git/admin/dispatch-status": "5d8bdf7aea186f2a92003516e083f2fa743eec52d97c242aee966d35b55594ff",
    "GET /api/v1/git/admin/drain-state": "61b348f13c41e2159d14ac253069fc20ac3f8adc77fde502d0aac2cc9b7fba44",
    "GET /api/v1/git/admin/lease-snapshot": "ac1d5249fdbe3097061158331be0e60129f9a4aa6451ee470cf2351db6022ec4",
    "GET /api/v1/git/admin/liveness": "736675bc3c3f3034c982c3e8d9e4a53f8a991995579610fd0309a55cd28e0204",
    "GET /api/v1/git/reachable": "f8768ad0aaac86d697463c04d7be35375b2731cc218bfd555b350cb0bfb16c3d",
    "GET /api/v1/mechanical-land/runs/{run_id}": "bd73fec6e0245233bea81f2dcc3e9047538d3615b8bc2d2c636a47fa83333f47",
    "GET /health": "5f4895dbe10f8bab0b97a798718236e7aa9bdf31b005093f009344ad42b23936",
    "POST /api/v1/cursor/api-role-dispatch": "244b05c956edd4a1e7ef7328aaec852382c86ffe8cec95c0a5f81af26f128c42",
    "POST /api/v1/cursor/branch-discharge": "1795f9ddd26b59147abc55390e10f80b464f914e59b0c04327facce713c30f9d",
    "POST /api/v1/cursor/claim": "651520d53462f3a7b18da8dc50406d22f36eb6ea1fcfe3205e86e67b6db4c319",
    "POST /api/v1/cursor/claim/{claim_id}/release": "2eb5884254e561966cc3854dca24852e7de30035d066c3079bae903b2bb581da",
    "POST /api/v1/cursor/claim/{claim_id}/update": "324510adeb76bacdf9adac7198dfa2cbb9e995d4f99549c1b9c7335a845cc045",
    "POST /api/v1/cursor/dispatch": "a09a8cbcf60307c6e0c65b560773f20dc5e33a80109bb2b110710b524685c9c9",
    "POST /api/v1/cursor/dispatch/{dispatch_id}/inject": "3a7485b92cb8beb9e0c2068dc4e397df3281957022243e8607517bd2ab726e0a",
    "POST /api/v1/cursor/dispatch/{dispatch_id}/park": "833754b851acb6b1232ed35371ae765541a06b5bf3cc72dc27c4174599dc62cb",
    "POST /api/v1/cursor/lane-worktree/release": "83917976539a00f911c9da30ec106021a977abc08d8adba6b7d4d4876415120e",
    "POST /api/v1/cursor/park-for-restart": "116a176ae0192ffa42a7748de0379cfa6e4e722e8842687c62d125ddbc093bac",
    "POST /api/v1/cursor/registry/heartbeat": "69c3d7e5b2084b523f64c6f7c3c5ee050a244fb056e95137a2d0fc659e52ecc5",
    "POST /api/v1/cursor/registry/record": "61e446e9e044fe31f243a2273d869c6a25e2e453638dc32ef008be5edad5344f",
    "POST /api/v1/cursor/registry/terminal": "2576948358bf42f3b6d23c2e6b0b1ada1c00a48cafcb8614ad5700c2f9b79e0e",
    "POST /api/v1/git/admin/begin-drain": "64b794df0b14f949b355b24bd8a7bfaeb1182c9d4ddf495eb91a149fc02026be",
    "POST /api/v1/git/admin/cancel-drain": "fa28d738a6a743894ce790233852d0167572b3e18ca3989e79ee31d179169d0b",
    "POST /api/v1/git/admin/cse-holder/release": "2bcd4bd7bc98ccad8f3fe3e95506017f72760fe62067f1e8d59de7c40ac93575",
    "POST /api/v1/git/admin/cse-holder/upsert": "b58e417eef4b0182131c3434e8c79d5944c7eb3b5bdca37681b68c73fb86ccfb",
    "POST /api/v1/mechanical-land/runs": "e7794ec17ef08f219d97bb4c151d11c96b99f0242edda81b4fc9adefd2b39fb4",
    "POST /api/v1/triggers/{trigger_id}/revoke": "9240662daadef7bc6b2255fbcab6755107ef7171d3a157f7ecab01ce3cfff76d",
}
