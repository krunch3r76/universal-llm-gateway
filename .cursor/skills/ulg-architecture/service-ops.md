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

## Periodic work — plane selection (`[ulg:periodic-plane]`)

**Invariant:** manage owns **two** scheduling planes with **different liveness**, and ¬cron/¬systemd-timer on this host (no `.timer` unit exists; `crontab` unused).

| Plane | Liveness | Examples |
|---|---|---|
| **manage TUI in-process loop** | Dies on `q` / TUI quit | `DigestTickLoop` (30s), `CharterRunnerTickLoop` (20s tick / 300s reconcile) |
| **manage-hosted host-service loop** | **Outlives** the TUI — `service_ctl` spawns via `host_spawn.spawn_detached_host_process` (`Popen` + `start_new_session`, `todo:manage-quit-must-not-stop-fleet`) | GIW `supervise(app, attr, factory)` loops; cortex-api lifespan `asyncio.create_task` (`run_skill_graph_drift_monitor`, 3600s) |
| **Satellite loop** | Independent host | cdp-ask `RegistryHygieneLoop` (1200s) |

`TUI_loop_liveness ⊊ host_service_liveness` — both are manage-owned, so choosing a host-service loop is a **refinement** of "manage is the scheduling plane", not an overturn.

**Cadence rule (BINDING):** `cadence ≫ process_lifetime ⇒ durable_due_state`. Observed lifetimes are **hours**, not days (2026-08-11, host up 46d: manage TUI 1h25m · GIW 1h20m · cortex-api 2h47m — routine `sync_restart` churn). A bare `asyncio.sleep(interval)` loop therefore fires with probability ≈0 for any daily/weekly cadence: every restart resets the sleep. Such a job is scheduled-on-paper and never runs.

| Cadence | Shape |
|---|---|
| seconds–minutes | bare `while True: … await asyncio.sleep(interval)` — the digest/drift template |
| hours–days | **short poll + persisted `last_run_at` due-check** (state under `~/.gateway/`), ¬ long sleep |

Do not copy `DigestTickLoop` as a template for a slow cadence: its 30s interval sits far **below** process lifetime, which makes restart amnesia invisible in that design. Durable-due-state precedent inside the fleet: GIW `trigger_service` (SQLite `fire_at` / `recur_every_s`, polled every 30s).

**Selection:** unattended ∧ hours–days ⇒ host-service loop + durable due-state · interactive ∨ work-advancing ∧ sub-minute ⇒ TUI loop · domain locality (credentials, SDK runtime, owning service) breaks ties.

## Forbidden ops

¬`pkill`, `docker restart/stop`, `systemctl`, direct script starts **against fleet services** — use `manage` MCP or `./manage` TUI. ¬ `systemctl` at system scope, ever. `systemctl --user` on auxiliary seat-authored units outside the `manage` roster is permitted; roster + test: `.cursor/rules/core_ws.mdc` § Lifecycle authority.

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
3. Authoritative check: `GET /health` shows `deploy_mode == source_synced` and recent `source_synced_at`. Routine sync copies the working tree, so read `source_sync_basis`, `code_version_semantics`, and `source_sync_worktree_state` before treating `code_version` as anything more than a checkout-HEAD ancestry label. ¬`docker images … Created` timestamp checks.

## Restart-window classification

Operator stop/restart/sync_restart and fleet Sync+Restart open a durable window in
`~/.gateway/restart-intents.db` **before** the first stop. During an open window,
`*_unreachable` is maintenance — check `manage(action="busy_status")` (MCP-alive)
or `scripts/model_manager/restart_window.py --json` (MCP-dark) before incident
fallback. Full seat rule: Use the `service-lifecycle` skill § Restart-window classification.
