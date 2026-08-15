---
trigger_match_terms: ["service-lifecycle", "service_lifecycle", "start", "stop", "restart", "gateway", "service", "rebuild", "wait_healthy", "services", "sync_restart", "manage"]
---

# Skill: Service Lifecycle (manage)

**Trigger**: ∀ service operation on gateway-ecosystem services — status, start, stop, restart, sync_restart, wait_healthy — or the post-code-change deploy loop.

**Portability**: this body is portable — the `manage` MCP tool is a portable primary any MCP-capable seat invokes (web parity confirmed, boot assertion a21537). Cursor-IDE-local affordances (shell health-probe, the `./manage` TUI, `pkill`/`docker` prohibitions, service deployment topology) live in the paired cursor rule `services_ws.mdc`.

---

## Authority

All service operations go through the `manage` MCP tool, which talks to `manage.sock`. There is no supported path that bypasses it.

**Related skills** (load by canonical slug on trigger):

- `ulg-architecture` § Service Lifecycle — the architectural rule this skill operationalizes (`∀ service operations: via manage MCP tool ∨ ./manage TUI`); also covers per-service rebuild strategy and MCP rebuild verification.
- `architecture-invariants` — universal architectural invariants these operations must respect (sole-maintainer constraint, transport, change scope).
- `mcp-surface-change` — the deploy path when the change is an MCP surface edit.

## Prerequisite

`manage.sock` exists only when `./manage` is running.

**Canonical path:** `MANAGE_SOCKET` from `transport_utils` — default `/tmp/universal-protocol/manage.sock` (override via `MANAGE_SOCKET` env var). Also emitted in `~/.gateway/topology.yaml` as `manage_socket` when `./manage` refreshes topology.

**Agent invariant:** use MCP `manage(action="status")` or `manage(action="busy_status")` for liveness — **not** raw `test -S` on guessed paths. If `manage` returns "manage.sock not found", the `./manage` process is not running — seat recycles via tmux `0:0` per hub rule `services_ws.mdc` § Manage process recycle (¬ park on operator).

## Tool usage

| Action | Example |
|--------|--------|
| Survey status | `manage(action="status")` |
| Deploy source + wait (host processes) | `manage(action="sync_restart", service=X)` then `manage(action="wait_healthy", service=X, timeout=120)` |
| Deploy source (mcp container) | `manage(action="sync_restart", service="mcp")` then `manage(action="wait_healthy", service="mcp", timeout=120)` |
| Restart | `manage(action="restart", service="rag")` — for **mcp**, restart is aliased to sync_restart (source docker-cp'd into `/app` first) |
| Start / stop | `manage(action="start", service="mcp")`, `manage(action="stop", service="gateway")` |
| Fleet charter hold | `charter_pause` / `charter_resume` / `charter_hold_status` — global admit stop for manage quit/start |
| Per-root charter hold | `charter_block_root` / `charter_unblock_root` / `charter_root_status` — ledger BLOCKED on one root; ¬ bus NOTE. Detail: hub `services_ws.mdc` § Charter control |

## Source-edit routing — which service to restart

∀ source file change: use this table to determine the correct deploy action.

| Source directory | Service | Deploy action |
|---|---|---|
| `libs/cortex_store/` | `cortex_api` | `sync_restart cortex_api` |
| `libs/agent_bus_store/` | `agent_bus` | `sync_restart agent_bus` |
| `libs/event_store/` | `event_service` | `sync_restart event_service` |
| `services/mcp-server/` | `mcp` | `sync_restart mcp` (~20s) |
| `services/_universal-llm-gateway/` | `gateway` | `sync_restart gateway` |
| `services/universal-stargate/` | `stargate` | `sync_restart stargate` |
| `services/rag/` | `rag` | `sync_restart rag` |
| `services/git_integration_worker/`, `libs/git_integrate/` | `git_integration_worker` | `sync_restart git_integration_worker` |
| `services/mcp-server/tools/git_integrate.py` | `mcp` **and** `git_integration_worker` | restart both after git MCP tool changes |

**Critical distinction**: `mcp` is a Docker container (`universal-mcp-server:local`); source is baked at `/app/` inside the image. The bind mount `/mnt/torus/projects:/data/project` does NOT reach `/app` — source edits are invisible to the running container without a rebuild. `sync_restart cortex_api` restarts only the Cortex API host subprocess and does NOT affect the MCP container. These are two entirely separate services.

## Services and rebuild

| Service | Deploy action for source edits |
|---------|--------|
| gateway | `sync_restart gateway` (source is bind-mounted into container) |
| mcp | `sync_restart mcp` (~20s). `rebuild mcp` is FORBIDDEN — manage returns: *"rebuild is forbidden for 'mcp'. Use manage(action='sync_restart', service='mcp') to deploy code changes — that path is the cached/bind-mount equivalent (~20s for mcp, instant for gateway via bind mount). A 'rebuild' here would do a full --no-cache build, which is ops-only via TUI: ./manage → Services → Build Image, and only valid when the inference engine, pip dependencies, or the Dockerfile itself change."* |
| stargate, rag, cloud_proxy | `sync_restart <service>` (host processes) |
| event_service, cortex_api, agent_bus, email_bridge | `sync_restart <service>` (host subprocesses; restart-equivalent) |

## Post-code-change loop (mandatory)

For **host-process services** (cortex_api, agent_bus, event_service, stargate, rag, cloud_proxy):
1. `quality_gate(files=[...])`
2. `manage(action="sync_restart", service=X)`
3. `manage(action="wait_healthy", service=X, timeout=120)`
4. `pipeline(...)` or other verification

For **mcp** (container service):
1. `quality_gate(files=[...])`
2. `manage(action="sync_restart", service="mcp")`
3. `manage(action="wait_healthy", service="mcp", timeout=120)`
4. Verify the deploy landed — `/health` must report `deploy_mode":"source_synced"` with a fresh `source_synced_at` after the sync_restart. `deploy_mode":"image_only"` means the container has not been source-synced since last image build. (The shell probe form is cursor-local — see `services_ws.mdc`.)

Never skip `wait_healthy` after start/restart/sync_restart.

For an ordinary live claim, the loop above is sufficient. For a
`live@<sha>` claim, commit the deployment paths path-explicitly **before**
`sync_restart`, then retain the restart and health payloads and verify live
`code_ref_satisfied` (equal or ancestor), process identity movement, and
relevant dirty-path disclosure. A dirty checkout may still be restarted; report
ordinary `live` plus tree state instead of exact `live@<sha>`. This ordering
qualifies the claim, not the restart.

## Invariants (FOL)

∀ service op: via `manage` MCP tool (∨ `./manage` TUI) — ¬ direct start scripts.
∀ source edit: correct `sync_restart <service>` per the routing table ∧ `wait_healthy` after.
∀ agent: ¬ `manage(action="rebuild", service="mcp")` — TUI Build Image only.
∀ liveness check: `manage(action="status")` — ¬ raw `test -S` on guessed socket paths.

## Restart-window classification (mandatory)

Operator-initiated stop/restart/sync_restart and fleet Sync+Restart operations open a
durable **restart window** in `~/.gateway/restart-intents.db` before the first stop.
Unreachable errors during an open window are **maintenance**, not incidents.

**Before classifying `*_unreachable` as incident or falling back to agent_bus+CDP:**

| Seat state | Check |
|------------|-------|
| MCP-alive | Read `manage(action="busy_status")` — per-service `restart_window` or top-level `restart_windows.open` |
| MCP-dark | Host CLI `scripts/model_manager/restart_window.py --json` (zero MCP dependency) |

When `restart_in_progress=true` (or equivalent window row present): honor `retry_after_s`,
bounded backoff until `window_deadline`, then re-probe. Only escalate to incident after
the window clears or the deadline passes with no healthy recovery.

Annotated transport errors include `restart_in_progress`, `retry_after_s`, and
`window_deadline` (e.g. pipeline/frontier `stargate_unreachable` during fleet restart).
Bare `*_unreachable` with no window → hard incident (crash / unplanned outage).
