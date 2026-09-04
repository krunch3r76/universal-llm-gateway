---
trigger_match_terms: ["jupiter-browser-bringup", "bring up chrome", "start jupiter browser", "web-fetcher not running", "cdp-ask not running", "chrome cdp down", "jupiter-cdp.target", "jupiter chrome down", "ECONNREFUSED 9222"]
description: Bring up Chrome + web-fetcher + cdp-ask on Jupiter for browse/project_ask. Read when browse/save_to fails with a connection error — infra launch, status check, bootstrap; not usage.
---

# Jupiter Browser Bring-up — Agent Guide

Start and verify Chrome, web-fetcher, and cdp-ask on Jupiter so the `browse` /
`project_ask` MCP tools have a live CDP session to talk to. This skill covers
**infrastructure only** — for how to actually use `browse` (downloads,
actions, screenshots), read `jupiter-browser-via-mcp`.

**Tested**: Chrome/147.0.7727.101 on Jupiter, April 2026.

## Launching (systemd on Jupiter)

Standing stack (Xvfb, fleet/messages/ess lanes, web-fetcher, cdp-ask):

```bash
ssh <user>@jupiter 'systemctl --user start jupiter-cdp.target'
```

Single lane (on-demand or non-standing pins such as gopuff/uber):

```bash
ssh <user>@jupiter 'cdp-lane-ensure fleet'    # or messages, ess, gopuff, uber
```

Status without starting:

```bash
ssh <user>@jupiter 'systemctl --user status jupiter-cdp.target'
ssh <user>@jupiter 'curl -sf http://127.0.0.1:9222/json/version && curl -sf http://127.0.0.1:8765/health && curl -sf http://127.0.0.1:8770/health'
```

Install units once per Jupiter checkout: `services/jupiter-cdp/install.sh`.
Fresh start ~5s; idempotent when units are already active.

---

## Architecture

```
dispatch(tool="browse", ...)   ← agent call
    ↓  WEB_FETCHER_URL=http://jupiter:8765  (set in ~/.gateway/mcp.yaml)
MCP server container
    ↓  HTTP POST /fetch
web-fetcher service on Jupiter  (port 8765, FastAPI, libs/web_fetcher/)
    ↓  CDP  BROWSER_CDP_URL=http://127.0.0.1:9222
Chrome on Jupiter  (DISPLAY=:2 fleet, :3 messages/ess/gopuff/uber — Xvfb-backed)
```

```
project_ask(op="submit", ...)  ← agent call
    ↓  PROJECT_ASK_URL=http://jupiter:8770  (set in ~/.gateway/mcp.yaml)
MCP server container
    ↓  HTTP POST /project-ask/submit
cdp-ask satellite on Jupiter  (port 8770, FastAPI, libs/cdp_ask/)
    ↓  CDP via claude_bundles registry pool
Chrome on Jupiter  (same :9222 session as browse)
    ↓  harvest archive → cortex://… under CORTEX_FILES_ROOT
```

`WEB_FETCHER_URL` and `PROJECT_ASK_URL` are configured in `~/.gateway/mcp.yaml`.
You only need to ensure Chrome, web-fetcher, and cdp-ask are running on Jupiter.

**Critical**: `browse` is NOT a top-level MCP tool — it lives under `dispatch`.
`CallMcpTool("browse")` fails. The correct call is always:
```
dispatch(tool="browse", arguments='{"url":"...", "mode":"browser"}')
```

---

## Quick Status Check (run first)

```bash
ssh <user>@<satellite-host> 'bash -s' << 'EOF'
echo "=== Chrome CDP ==="
curl -sf http://localhost:9222/json/version \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['Browser'])" \
  || echo "NOT running"
echo "=== web-fetcher ==="
curl -s http://localhost:8765/health || echo "NOT running"
echo "=== cdp-ask ==="
curl -s http://localhost:8770/health || echo "NOT running"
EOF
```

Expected when healthy:
- Chrome: `Chrome/147.0.7727.101`
- web-fetcher: `{"status":"ok","concurrent_limit":3,"headless":null,"cdp_url_configured":true}`
- cdp-ask: `{"status":"ok","harvest_root":"/mnt/torus/mcp-data/files","harvest_root_ok":true}`

> **Note**: `cdp_url_configured: true` only means `BROWSER_CDP_URL` env var is
> set — it does NOT confirm Chrome is running. Always probe port 9222 directly.

---

## Bring Up Chrome (lane unit)

Prefer `cdp-lane-ensure` or systemd — do not hand-launch Chrome with `pkill`/`nohup`.

**Fleet lane** (port 9222, DISPLAY `:2`, claude.ai profile):

```bash
ssh <user>@jupiter 'cdp-lane-ensure fleet'
```

**On-demand lanes** (messages, ess, gopuff, uber):

```bash
ssh <user>@jupiter 'cdp-lane-ensure gopuff'   # :3, port 9270
ssh <user>@jupiter 'cdp-lane-ensure messages'  # :3, port 9250
```

Restart a stale lane without touching other pins:

```bash
ssh <user>@jupiter 'systemctl --user restart cdp-lane@fleet.service'
```

Verify CDP after ensure:

```bash
ssh <user>@jupiter 'curl -sf http://127.0.0.1:9222/json/version \
  | python3 -c "import sys,json; print(json.load(sys.stdin)[\"Browser\"])"'
```

Typical output: `Chrome/147.0.7727.101`

**Notes**:
- Standing lanes use per-pin profiles under `~/.gateway/` and `~/sms-bridge/profiles/`
- Xvfb displays `:2` and `:3` are started by `jupiter-cdp.target` via `jupiter-cdp-xvfb@.service`
- Lane units survive SSH disconnect (systemd user session)

---

## Bring Up web-fetcher

web-fetcher is a systemd user unit; it requires the fleet lane (9222) to be up first.

```bash
ssh <user>@jupiter 'cdp-lane-ensure fleet && systemctl --user start web-fetcher.service'
```

Status / restart:

```bash
ssh <user>@jupiter 'systemctl --user status web-fetcher.service'
ssh <user>@jupiter 'systemctl --user restart web-fetcher.service'
curl -sf http://jupiter:8765/health
```

Expected: `{"status":"ok","concurrent_limit":3,"headless":null,"cdp_url_configured":true}`

---

## cdp-ask activation (project_ask)

cdp-ask is started by `jupiter-cdp.target` (Wants-only; not upheld). It serves
MCP `project_ask` (sealed CDP consults with cortex harvest). Requires Chrome
:9222 **and** `CORTEX_FILES_ROOT=/mnt/torus/mcp-data/files` on Jupiter.

### Production activation runbook

1. Jupiter: `git pull` in the ULG checkout (confirm `libs/cdp_ask/` present).
2. Jupiter: `services/jupiter-cdp/install.sh` if units changed; then
   `systemctl --user start jupiter-cdp.target` (or `cdp-lane-ensure fleet` first).
3. Verify: `curl -sf http://jupiter:8770/health` → `harvest_root_ok:true`.
4. Hub: set `PROJECT_ASK_URL: http://jupiter:8770` in `~/.gateway/mcp.yaml`;
   remove/comment any `host.docker.internal` dev URL (fail-closed).
5. Hub: `./scripts/sync-and-restart-mcp.sh` — **do not** use `--no-cache` unless
   you immediately re-sync G3 paths (`libs/cdp_ask/`, `services/mcp-server/tools/project_ask.py`)
   via the routine sync path or explicit `sync_source_into_container`. The
   `--no-cache` branch recreates the container without post-up docker cp.
6. Verify: `docker exec mcp-server printenv PROJECT_ASK_URL` and
   `docker exec mcp-server curl -sf http://jupiter:8770/health`.
7. Smoke: `project_ask(op="submit", prompt_text="Reply OK", converse=true,
   no_project_uuid=true)` → poll until `archive_uri` is set (no
   google-chrome/unreachable errors in poll payload).

### Bring up cdp-ask manually (if needed)

cdp-ask is started via `jupiter-cdp.target` or explicitly:

```bash
ssh <user>@jupiter 'systemctl --user start cdp-ask.service'
# or from hub manage MCP: manage(action="start", service="cdp_ask")
```

Verify:

```bash
ssh <user>@jupiter 'curl -sf http://127.0.0.1:8770/health'
```

Expected: `{"status":"ok","harvest_root":"/mnt/torus/mcp-data/files","harvest_root_ok":true}`

> **Keep-alive**: cdp-ask is a forking systemd unit — survives SSH disconnect.
> Re-check `:8770/health` after disconnect to confirm.

---

## Full Bootstrap (when starting from scratch)

```bash
ssh <user>@jupiter 'systemctl --user start jupiter-cdp.target'
# on-demand lane example:
ssh <user>@jupiter 'cdp-lane-ensure gopuff'
```

Verify standing stack:

```bash
ssh <user>@jupiter 'bash -s' << 'EOF'
echo "--- fleet CDP ---"
curl -sf http://127.0.0.1:9222/json/version \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['Browser'])" \
  || echo "fleet NOT ready"
echo "--- web-fetcher ---"
curl -s http://127.0.0.1:8765/health || echo "web-fetcher NOT ready"
echo "--- cdp-ask ---"
curl -s http://127.0.0.1:8770/health || echo "cdp-ask NOT ready"
EOF
```

---

## Verifying End-to-End from MCP

After starting Chrome and web-fetcher, confirm the full chain works:

```python
dispatch(tool="browse", arguments='{"url":"https://example.com","mode":"browser","max_chars":200}')
```

Expected response:
```json
{
  "tool": "browse",
  "result": {
    "url": "https://example.com/",
    "title": "Example Domain",
    "content": "This domain is for use in documentation examples...",
    "method": "browser",
    "cf_bypassed": false
  }
}
```

If `"method": "browser"` appears — Chrome via CDP is working.

---

## Failure Modes and Fixes (infra)

| Symptom | Cause | Fix |
|---|---|---|
| `dispatch(tool="browse")` → `WEB_FETCHER_URL not configured` | Env var missing in MCP container | Check `~/.gateway/mcp.yaml` has `WEB_FETCHER_URL: "http://jupiter:8765"` and rebuild MCP |
| `dispatch(tool="browse")` → connection error to `jupiter:8765` | web-fetcher not running | `systemctl --user start web-fetcher.service` (after `cdp-lane-ensure fleet`) |
| `BrowserType.connect_over_cdp: connect ECONNREFUSED 127.0.0.1:9222` | Fleet lane not running | `cdp-lane-ensure fleet`; then restart web-fetcher |
| `cdp_url_configured: true` but browser fetches fail | web-fetcher env var set but Chrome is down | The health field is misleading — check CDP directly with `curl http://localhost:9222/json/version` |
| Chrome crashes or becomes unresponsive | Memory pressure or JS crash | `systemctl --user restart cdp-lane@fleet.service` (or the affected lane) |
| SSH session drops mid-launch | Network blip | Units survive via systemd user session; verify with `systemctl --user status cdp-lane@fleet.service` |
| Stale Chrome occupying port 9222 | Previous lane unit wedged | `systemctl --user restart cdp-lane@fleet.service` |
| Stale process on port 8765 | Old web-fetcher still bound | `systemctl --user restart web-fetcher.service` |
| CDP WebSocket → 403 Forbidden | Chrome launched without `--remote-allow-origins=*` | Lane launch script sets this; restart the lane unit |

For download-specific and browse-usage failure modes (garbled text, `save_to`
returning HTML, action timeouts, WAF challenges), read `jupiter-browser-via-mcp`
and `browse-waf-and-pagination-gotchas` instead — this table covers only
"the infra chain isn't up."

---

## Related Files

| Path | Purpose |
|---|---|
| `services/jupiter-cdp/jupiter-cdp.target` | **Systemd target** — standing Xvfb + lanes + web-fetcher + cdp-ask |
| `services/jupiter-cdp/cdp-lane-ensure` | Ensure one lane unit is active and CDP listens |
| `scripts/cdp-ask` | cdp-ask satellite entry point (port 8770) |
| `libs/cdp_ask/` | cdp-ask FastAPI app + CDP project-ask runner |
| `services/mcp-server/tools/project_ask.py` | MCP `project_ask` relay to PROJECT_ASK_URL |
| `libs/web_fetcher/` | web-fetcher FastAPI app + Playwright browser module |
| `libs/web_fetcher/app.py` | FastAPI surface — `FetchRequest` schema, routing logic |
| `scripts/web-fetcher` | Low-level web-fetcher entry point (via `jupiter-web-fetcher` unit wrapper) |
| `docker/compose/mcp-server.yml` | Declares `WEB_FETCHER_URL` + `MCP_SHARED_IMAGE_DIR` env vars |
| `~/.gateway/mcp.yaml` | Sets `WEB_FETCHER_URL: "http://jupiter:8765"` and `PROJECT_ASK_URL: "http://jupiter:8770"` |

## Related skills

`jupiter-browser-via-mcp` (usage: browse, save_to, downloads, actions) ·
`browse-waf-and-pagination-gotchas` (WAF/response-store/pagination diagnosis) ·
`web-automation-discipline` (interactive form automation on top of this transport).

## Source

Split out of `jupiter-browser-via-mcp` (2026-09-01, agent-bus:9853 G5) — this
file holds exactly the bring-up/infra content that skill originally carried
under `## Launching`, `## Architecture`, `## Quick Status Check`, `## Bring Up
Chrome`, `## Bring Up web-fetcher`, `## cdp-ask activation`, and `## Full
Bootstrap`. Content unedited beyond the split; original provenance (reconstructed
from Carvana Jupiter browsing session, cursor-2026-04-11-1934) lives with the
sibling skill.
