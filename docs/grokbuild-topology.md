# Grokbuild Topology (V2)

**Version:** 2.0
**Created:** 2026-05-22
**Status:** Active. Supersedes the V1 in-process model documented across the dispatch agent guide.

This document describes the **runtime call graph and process topology** for the grokbuild domain in V2. Deploy-specific instructions (systemd unit, compose file, env var override paths) live in `services/grokbuild_worker/README.md`; this doc is the cross-process reference.

---

## Process boundary diagram

```
┌──────────────────────┐
│  MCP-enabled caller  │  claude-web, cursor, oppie, orion, bard, claude-api
│  (any seat)          │
└──────────┬───────────┘
           │  MCP tool call: grok_build(op="build", ...)
           │  (or worktree_create / fetch_result / push / pr_create / build_status)
           ▼
┌──────────────────────────────────────────────────────────────────────┐
│  mcp-server (process #1)                                             │
│  services/mcp-server/tools/grokbuild.py                              │
│                                                                      │
│  Thin HTTP relay — no business logic, no validator, no runner.       │
│  Wraps op → REST path translation and forwards via                   │
│  make_async_client(DEFAULT_STARGATE_URL).                            │
└──────────┬───────────────────────────────────────────────────────────┘
           │  HTTPS (UDS in production):
           │  POST /api/v1/grokbuild/dispatches                  → 202 + Location
           │  GET  /api/v1/grokbuild/dispatches/{id}             → tracker status
           │  GET  /api/v1/grokbuild/dispatches/{id}/events      → SSE stream
           │  GET  /api/v1/grokbuild/dispatches/{id}/result      → fetch envelope
           │  DELETE /api/v1/grokbuild/dispatches/{id}           → cancel
           │  POST/GET/DELETE /api/v1/grokbuild/worktrees[/{n}]  → worktree ops
           │  POST /api/v1/grokbuild/worktrees/{n}/push          → push
           │  POST /api/v1/grokbuild/worktrees/{n}/pull-requests → PR
           │  GET  /api/v1/grokbuild/health                      → readiness
           │  GET  /api/v1/grokbuild/models                      → registry
           ▼
┌──────────────────────────────────────────────────────────────────────┐
│  universal-stargate (process #2)                                     │
│  services/universal-stargate/systems/proxy/routers/api/grokbuild.py  │
│                                                                      │
│  Forward proxy. Strips hop-by-hop headers, preserves method+body+    │
│  query+remaining headers, streams SSE/chunked responses through.     │
│  Auth: Stargate pass-through (no token added; no token checked here).│
│  Transport: make_async_client(_WORKER_BASE_URL) per                  │
│  [universal:transport] invariant.                                    │
└──────────┬───────────────────────────────────────────────────────────┘
           │  HTTP, default http://127.0.0.1:8090
           │  Override via GROKBUILD_WORKER_HOST / GROKBUILD_WORKER_PORT
           ▼
┌──────────────────────────────────────────────────────────────────────┐
│  grokbuild-worker (process #3)                                       │
│  services/grokbuild_worker/app.py — FastAPI                          │
│                                                                      │
│  Owns: validator, registry (persistent), tracker (in-memory),        │
│  runner, sidecar I/O, event emission. Imports libs/grokbuild.        │
│                                                                      │
│  Lifespan registers UDS event publisher into                         │
│  grokbuild.events_core.register_uds_publisher so lib events          │
│  (mcp.grokbuild.*) reach the event service from this process.        │
│                                                                      │
│  Tracker: in-memory dict, cap=4 concurrent, 24h TTL (operator-locked │
│  decision:grokbuild-execution-tracker-shape, assertion 10634).       │
└──────────┬───────────────────────────────────────────────────────────┘
           │  subprocess: asyncio.create_subprocess_exec
           │  cwd = worktree path  |  argv = grok ... -p PROMPT
           │  Process group isolated (start_new_session=True) so the    
           │  cancel path can SIGTERM/SIGKILL the whole group.          
           ▼
┌──────────────────────────────────────────────────────────────────────┐
│  grok CLI (process #4)                                               │
│  /home/io/.local/bin/grok (or GROK_BIN_PATH override)                │
│                                                                      │
│  External binary. Reads/writes via filesystem; emits NDJSON          │
│  on stdout; the worker captures it into the sidecar file.            │
└──────────────────────────────────────────────────────────────────────┘
```

---

## Ports and sockets

| Process | Listens on | Notes |
|---|---|---|
| `mcp-server` | (per existing topology — UDS) | Unchanged by V2. |
| `universal-stargate` | (per existing topology — UDS + HTTP) | Adds `/api/v1/grokbuild/*` forward route. |
| `grokbuild-worker` | **`127.0.0.1:8090`** TCP (default) | Override via `GROKBUILD_WORKER_HOST` / `GROKBUILD_WORKER_PORT`. Localhost-only by default — auth is Stargate pass-through, and the worker is not safe to expose without an upstream gate. |
| `grok` CLI | n/a (subprocess) | Spawned by worker; stdin closed, stdout/stderr piped. |

**Why TCP, not UDS, for the worker?** Two reasons:

1. The bare-metal-systemd deploy shape (preferred per A.2 plan) puts the worker outside any container, so the UDS namespace would not be visible to Stargate without bind-mounts.
2. The grok CLI's working directory may itself live under bind-mounted paths (`/mnt/torus/projects/*`), and grok writes to disk in those locations. Keeping the worker's transport agnostic to UDS keeps the deploy shape symmetric between bare-metal-systemd and container.

The Stargate proxy uses `make_async_client(_WORKER_BASE_URL)` so the transport invariant (`[universal:transport]`) is upheld even when the actual scheme is `http://` rather than UDS.

---

## Filesystem layout

The lib (`libs/grokbuild/*`) hardcodes nothing path-sensitive; everything is env-driven with operator-locked defaults that match the worker's `WorkerConfig`:

| Path | Purpose | Env var | Default |
|---|---|---|---|
| `/var/lib/grokbuild-worker/sidecars/` | NDJSON sidecar files (one per dispatch) | `GROKBUILD_SIDECAR_DIR` | `/var/lib/grokbuild-worker/sidecars` |
| `/var/lib/grokbuild-worker/registry.json` | Persistent in-flight cwd registry | `GROKBUILD_REGISTRY_PATH` | `/var/lib/grokbuild-worker/registry.json` |
| `/mnt/torus/projects/ulg-grok-worktrees/` | Worktree root | (constant) | `WORKTREE_ROOT` in `libs/grokbuild/worktree.py` |
| `/mnt/torus/projects/` | Allowed source-repo root | (constant) | `ALLOWED_SOURCE_ROOT` in `libs/grokbuild/worktree.py` |
| `/home/io/.grok/` | grok CLI auth dir | `GROK_AUTH_DIR` | `/home/io/.grok` |
| `/home/io/.local/bin/grok` | grok CLI binary | `GROK_BIN_PATH` | `/home/io/.local/bin/grok` |
| `/tmp/logs/grokbuild-worker/grokbuild-worker.log` | Worker stdout/stderr (when started via manage daemon) | (constant) | `_LOG_DIR` in `grokbuild_worker_service.py` |

**Operator-locked decision** (per assertion 10634, Phase A.2): the sidecar dir lives under `/var/lib/grokbuild-worker/` so the worker user (`io`) owns it cleanly across restarts. The V1 path (`/tmp/logs/grokbuild`) was retired in V2 — both `WorkerConfig` and the lib's `constants._SIDECAR_DIR` consult `GROKBUILD_SIDECAR_DIR` with the new default.

Operator must pre-create the worker-owned dirs once per host:

```bash
sudo install -d -o io -g io -m 0750 /var/lib/grokbuild-worker
sudo install -d -o io -g io -m 0750 /var/lib/grokbuild-worker/sidecars
```

The lifespan does best-effort `mkdir -p`; insufficient privileges → `grokbuild.worker.degraded` event + `status=degraded` on `/health`, but the worker still boots so the operator can inspect.

---

## Lifecycle: one async build dispatch

The async build surface (`op="build"`) returns a 202 envelope with `dispatch_id` immediately; the actual grok CLI invocation runs in a tracker-owned task. Callers must poll status, stream events, or fetch results explicitly.

```
T0  Caller → mcp-server: grok_build(op="build", cwd=..., prompt=...)
T0  mcp-server → Stargate: POST /api/v1/grokbuild/dispatches  (body = lib request shape)
T0  Stargate → worker: same POST forwarded
T0+ Worker validates request shape (Pydantic), runs admission via libs/grokbuild.validator
T0+ Tracker.start admits the request:
       - check cap (operator answer 1c: 4 concurrent)
         - if over cap → raise TrackerCapacityError
           - route mints rejection_id, emits grokbuild.dispatch.rejected
           - HTTP 429 + Retry-After: 30 returned to caller
           - mcp-server tool maps 429 → rejected envelope with reason_code=capacity_exhausted
           - STOP
       - mint dispatch_id (uuid4)
       - record Entry(state="pending") in tracker dict
       - publish grokbuild.dispatch.accepted on UDS event bus
       - fanout {"type":"accepted"} to any SSE subscribers
       - asyncio.create_task(run_dispatch_task)  — background task
T0+ HTTP 202 + body {dispatch_id, status_url, events_url, state:"pending"} → caller
       - Location: /api/v1/grokbuild/dispatches/{id}
T1  Background task transitions state → "running"
T1  dispatch_op invoked (libs/grokbuild/dispatch.py):
       - validator (already passed at admission, but re-run for consistency)
       - registry.try_acquire_cwd → may reject as dispatch_conflict
       - lib emits mcp.grokbuild.dispatch.called (via UDS bridge)
       - runner spawns grok subprocess, captures NDJSON sidecar
T2..N grok runs (seconds to ~30min for tier=max). Caller polls / streams meanwhile:
       - GET /dispatches/{id} → snapshot (pending / running / succeeded / failed / cancelled)
       - GET /dispatches/{id}/events → SSE stream (closes on terminal state)
T_end Runner returns envelope. Tracker projects onto Entry:
       - state → succeeded | failed | cancelled
       - lib emits mcp.grokbuild.dispatch.completed (with audit-rich payload)
       - tracker publishes grokbuild.dispatch.completed (SSE-friendly)
       - tracker.close_subscribers drains SSE queues with sentinel
       - Entry held in tracker for 24h (operator answer 1b) then TTL-purged
T_end+ Caller fetches the canonical envelope: GET /dispatches/{id}/result
       - tracker is not consulted here; the lib reads its sidecar directly
       - allows fetching results across worker restarts as long as the sidecar persists
```

**Crash recovery.** Worker restart loses the in-memory tracker dict (operator answer 1a). On boot:

* `registry._load_registry_from_disk` runs at module import. If the previous writer PID is dead, all entries are pruned and `mcp.grokbuild.registry.recovered` fires with the count.
* `tracker.cleanup_orphans` runs in the lifespan startup. With pure-in-memory storage this is usually a no-op (the dict is empty); tests pre-seed dead entries to exercise the path.
* In-flight dispatches at crash time have their grok subprocesses orphaned (the process group survives but no parent reads stdout). Operator should `pkill -g` the orphans or wait for them to exit; sidecars remain on disk and are still fetchable via `op="fetch_result"`.

**Backwards-incompatible note.** V1's `op="build"` was synchronous — the MCP tool blocked until the grok CLI exited and returned the envelope directly. V2 always returns 202; the relay does NOT poll on the caller's behalf. Callers MUST use the async surface (`op="build_status"`, `op="build_events"`, `op="fetch_result"`) explicitly. See `agent-skills/grokbuild-v2.md` for the caller-side discipline.

---

## Auth model

Stargate pass-through end-to-end. No token at any internal hop.

* MCP-side auth (caller identity, capability checks): Stargate's edge.
* Worker-side auth: **none** — the worker trusts requests arriving from Stargate. Same pattern as `cortex-api` and `agent-bus`.
* grok CLI auth: the binary uses `~/.grok/` (default `GROK_AUTH_DIR=/home/io/.grok`). Refresh via `grok login` from the worker user's shell — the worker's `_build_env()` strips most parent-process env but preserves `HOME`, so `~/.grok/` resolves correctly.

A failed `grok models` preflight at admission emits `missing_grok_auth` on `mcp.grokbuild.dispatch.rejected` and surfaces `auth_dir: missing` on `/health` checks.

---

## MCP runtime env contract (Phase B)

Subprocess `grok-build` dispatches (process #4 in the diagram above) can call vortex MCP tools when the handoff's `<mcp_capabilities>` template authorizes it (`mcp_allowed_read_only` or `mcp_allowed_full`). MCP wiring is **not** injected by the grokbuild worker or Stargate; it comes from the grok CLI reading `~/.grok/config.toml` (default `GROK_AUTH_DIR=/home/io/.grok`).

| Surface | MCP-capable? | Config source | Endpoint |
|---|---|---|---|
| **Subprocess grok-build** (worker-spawned) | Yes | `~/.grok/config.toml` → `[mcp_servers.user-vortex]` | `https://mcp.k-1.me/mcp/grok` (per `mcp-registry.toml` `url_overrides.grok-direct`) |
| **grok-direct** (operator CLI in workspace) | Yes | Same `~/.grok/config.toml` | Same `/mcp/grok` URL |
| **API grok variants** (`xai/grok-4.3__effort_*`, `xai/grok-4.20-*`) | **No** | N/A — corpus must be pre-staged inline (Phase F) | — |

**Precedence / distinction.** Subprocess grok-build and grok-direct share the **identical** MCP configuration file and therefore the **identical** MCP availability on this host. The difference is operational, not configurational:

* **grok-direct** — operator-driven CLI seat in a workspace terminal; all MCP-requiring work runs in that session.
* **Subprocess grok-build** — dispatched asynchronously by the grokbuild worker (`grokbuild(op="build", ...)`) with `cwd` set to a worktree; MCP calls happen inside the child grok process using the same `config.toml`.

Neither surface receives MCP credentials from the worker's `_build_env()` allowlist unless the operator configures env-var expansion in TOML (see below).

### Bearer auth: literal vs env-var expansion

`mcp-registry.toml` projects `[mcp_servers.user-vortex.headers]` with `Authorization = "Bearer …"`. Two valid operator shapes:

| Shape | `config.toml` example | `_ALLOW` passthrough needed? |
|---|---|---|
| **Literal bearer** (current on this host) | `Authorization = "Bearer <token>"` | No — grok reads the token from disk at session start |
| **Env expansion** | `Authorization = "Bearer ${MCP_AUTH_TOKEN}"` | Yes — add `MCP_AUTH_TOKEN` (and any related `MCP_*` vars the TOML references) to `libs/grokbuild/runner_argv.py` `_ALLOW` so `_build_env()` forwards them into the subprocess |

The worker's `_build_env()` copies only keys in `_ALLOW` from the parent process, then forces `TERM=dumb`. Keys outside the allowlist (including `SECRET`, generic parent env) are stripped. `HOME` is preserved so `~/.grok/` resolves to the operator auth dir.

**Smoke acceptance (Phase B).** With `mode="read_only"` and `mcp_allowed_read_only` in `system_context`, a dispatch whose prompt issues `cortex(tool="entity_get", …)` should return HTTP 200 in the sidecar. Requires grokbuild-worker healthy (`GET /api/v1/grokbuild/health` via Stargate) and valid `~/.grok/config.toml` MCP headers.

---

## Deploy shapes

The FastAPI app is identical across shapes; only the supervisor differs.

| Shape | Supervisor | Pros | Cons |
|---|---|---|---|
| **Bare-metal systemd** (preferred per A.2) | `systemd --user` or system unit at `/etc/systemd/system/grokbuild-worker.service` | Direct access to host venv (`/home/io/.venvs/universal`); no bind-mount gymnastics; auto-restart on crash. | Requires the operator to install the unit file. |
| **Container (compose)** | `docker compose -f docker/compose/grokbuild-worker.yml up -d` | Self-contained; same image runs anywhere. | Bind-mounts must mirror host paths for `WORKTREE_ROOT`, `GROK_AUTH_DIR`, `/var/lib/grokbuild-worker`. |
| **Dev (manage daemon)** | `manage(action="start", service="grokbuild_worker")` via Claude TUI | Quick start/stop during development. | Logs land in `/tmp/logs/grokbuild-worker/`; restart on crash is manual. |

Switching shapes is operator-driven: stop the existing supervisor, start the new one. The lib + worker code path is invariant.

---

## Pointers

* Deploy specifics: `services/grokbuild_worker/README.md`
* Caller-side contract (V2 async surface): `agent-skills/grokbuild-v2.md`
* V1 caller-side reference: `agent-skills/grokbuild-v1.md`
* Event payloads + query examples: `docs/event-contracts.md` § Grok Build Dispatch Signals
* Operator decisions backing V2 shape: `cortex:decision:grokbuild-rest-execution-host` (assertion 10611), `cortex:decision:grokbuild-execution-tracker-shape` (10634), `cortex:plan:grokbuild-v2` (10633)
