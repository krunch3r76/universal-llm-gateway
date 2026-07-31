# Directory claims — 2026-07-31 multitask sessions

Append your claim before starting. Read this file first.

---

## Window 2 (Opus Max 1M, started 21:35Z / 14:35 local)

Brief: `cortex://notes/system/specs/opus-max-window2-brief-20260731.md`
Output dir: `tmp/reviews/opus-max-window2-20260731/`
Starting HEAD: `369ca56b`

Claimed for **write**:

| Path | Worker | Target |
|---|---|---|
| `libs/cortex_store/` | W1 | 3.1 cortex overhaul + write-op defects |
| `services/cortex-api/` | W1 | 3.1 cortex overhaul |
| `services/universal-stargate/systems/proxy/` | W6 | 3.5 stargate proxy overhaul slice |
| federation (path TBD by W6) | W6 | 3.5 federation overhaul |
| `tmp/reviews/opus-max-window2-20260731/` | all | deliverables |
| `/mnt/torus/mcp-data/files/notes/system/threads/window2-*` | coordinator | operator channel |

Claimed **read-only** (investigation only, no edits):

| Path | Worker | Why read-only |
|---|---|---|
| `scripts/model_manager/` | W5 | window-1 partition says not ours |
| `services/git_integration_worker/` | W3, W4 | window-1 partition says not ours |
| `config/mcp/`, `services/mcp-server/` | — | window-1 partition says not ours |

Not touched by window 2: `libs/charter_runner_store/`.

Pre-existing uncommitted edits in the tree at window-2 start, **left alone**
(not authored by this window, per shared-checkout-housekeeping):
`libs/claude_bundles/cdp_model_endpoint.py`, `libs/claude_bundles/cdp_progress_trace.py`,
`libs/cortex_store/main.py`, `libs/deploy_identity/code_version.py`,
`services/universal-stargate/systems/frontier_consult/cdp_generate_reconcile.py`.

`libs/cortex_store/main.py` is dirty at window-2 start and falls inside W1's
claim — W1 must diff it and preserve, never revert.
