# Service ops — deferred reference

Load when restarting/rebuilding services, MCP dependency changes, or manage-socket troubleshooting.

## Host process model (`[ulg:host-process]`)

**Invariant:** except **satellites** (e.g. Jupiter `cdp-ask`), ULG host fleet services run as **`./manage` subprocesses** via `service_ctl` / host spawn — start, stop, `sync_restart`, health.

| Surface | Role |
|---|---|
| **manage-hosted host service** | GIW, stargate, cortex-api, agent-bus, event-service, RAG, … — sibling processes under manage |
| **manage TUI in-process loop** | charter-runner tick, digest tick — die when the manage process quits (different plane from host services) |
| **Satellite** | Remote/neighbor processes (cdp-ask on Jupiter) — carve-out from manage subprocess hosting |
| **Repo `systemd/*.service`** | Historical / optional unit files — **not** the live path on this deployment |

**Architecture consult duty:** when a packet binds service **home**, **extract**, or **process manager**, attach `ulg-architecture` + `architecture-invariants` and **inline** this invariant. Do not let binders inherit systemd framing from older BEFORE maps.

## Forbidden ops

¬`pkill`, `docker restart/stop`, `systemctl`, direct script starts. Use `manage` MCP or `./manage` TUI.

## Post-code-change loop

1. `quality_gate(files=[...])`
2. `manage(action="sync_restart", service=X)`
3. `manage(action="wait_healthy", service=X, timeout=120)`

Agent forbidden: `manage(action="rebuild", service="gateway"|"mcp")` — route engine/Dockerfile changes through `./manage` → Build Image.  
`manage.sock` missing ⇒ ask operator to start `./manage`. Socket: `transport_utils.MANAGE_SOCKET`.

## Per-service sync_restart

| Service | Strategy |
|---|---|
| `gateway` | Restart (bind-mounted source) |
| `mcp` | Source-sync via `scripts/sync-and-restart-mcp.sh` + restart |
| `stargate`, `rag`, `cloud_proxy`, `cortex_api`, `agent_bus`, `event_service` | Restart |

New MCP `requirements.txt` dependency ⇒ TUI Build Image or `scripts/sync-and-restart-mcp.sh --no-cache`.

## MCP sync verification

`sync_restart` for MCP runs `docker cp` source-sync + restart — it does NOT rebuild the image.

1. `manage(action="sync_restart", service="mcp")` — restart deferred ~30s.
2. During window: calls may return `-32099` `server_restarting` / `Retry-After: 30` — retry or `manage(action="wait_healthy", service="mcp")`.
3. Authoritative check: `GET /health` shows `deploy_mode == source_synced` and recent `source_synced_at`. ¬`docker images … Created` timestamp checks.

## Restart-window classification

Operator stop/restart/sync_restart and fleet Sync+Restart open a durable window in
`~/.gateway/restart-intents.db` **before** the first stop. During an open window,
`*_unreachable` is maintenance — check `manage(action="busy_status")` (MCP-alive)
or `scripts/model_manager/restart_window.py --json` (MCP-dark) before incident
fallback. Full seat rule: Use the `service-lifecycle` skill § Restart-window classification.
