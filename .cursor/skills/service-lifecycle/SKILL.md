---
name: service-lifecycle
description: Start, stop, restart, rebuild, or wait_healthy for gateway services via MCP. Use when the user asks to restart a service, rebuild gateway/mcp, wait for healthy, or run the post-code-change loop. Requires ./manage running (manage.sock).
---

# Service lifecycle (manage)

**Authority**: All service operations go through `./manage` (TUI or API). The agent uses the `manage` MCP tool, which talks to manage.sock. Full rules: `service-lifecycle_ws.mdc`, `mcp-integration_ws.mdc`.

**Related cortex skills**:

- `cortex:agent-skills/ulg-architecture.md` §Service Lifecycle — the architectural rule this skill operationalizes (`∀ service operations: via manage MCP tool ∨ ./manage TUI`); also covers per-service rebuild strategy and MCP rebuild verification (the three-step image-timestamp / container-start-time / mcp.oauth.server.started signal check).
- `cortex:agent-skills/architecture-invariants.md` — universal architectural invariants this skill's operations must respect (sole-maintainer constraint, transport, change scope).

## Prerequisite

`manage.sock` exists only when `./manage` is running (TUI or future headless).

**Canonical path:** `MANAGE_SOCKET` from `transport_utils` — default
`/tmp/universal-protocol/manage.sock` (override via `MANAGE_SOCKET` env var).
Also emitted in `~/.gateway/topology.yaml` as `manage_socket` when `./manage`
refreshes topology.

**Agent invariant:** use MCP `manage(action="status")` or
`manage(action="busy_status")` for liveness — **not** raw `test -S` on guessed
paths. Forbidden probes: repo root (`…/universal-llm-gateway/manage.sock`),
`~/.gateway/manage.sock` (other configs live there; this socket does not).

If `manage` returns "manage.sock not found", ask the user to start `./manage`.

## Tool usage

| Action | Example |
|--------|--------|
| Survey status | `manage(action="status")` |
| Deploy source + wait (host processes) | `manage(action="sync_restart", service=X)` then `manage(action="wait_healthy", service=X, timeout=120)` |
| Deploy source (mcp container) | `manage(action="sync_restart", service="mcp")` then `manage(action="wait_healthy", service="mcp", timeout=120)` |
| Restart | `manage(action="restart", service="rag")` — for **mcp**, restart is aliased to sync_restart (source docker-cp'd into `/app` first) |
| Start / stop | `manage(action="start", service="mcp")`, `manage(action="stop", service="gateway")` |

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
| `services/grokbuild_worker/`, `libs/grokbuild/` (worker-facing) | `grokbuild_worker` | `sync_restart grokbuild_worker` |
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
4. Verify via `curl -sk https://mcp.k-1.me/health` — expect `deploy_mode":"source_synced"` and a recent `source_synced_at` after sync_restart (or restart, which is aliased). `deploy_mode":"image_only"` means the container has not been source-synced since last image build.

Never skip `wait_healthy` after start/restart/sync_restart. Never use `pkill`, `docker restart`, or direct start scripts.
