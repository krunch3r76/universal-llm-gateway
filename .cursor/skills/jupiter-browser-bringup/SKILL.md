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
Chrome on Jupiter  (DISPLAY=:1, cosmic-comp Wayland session)
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

## Bring Up Chrome

**Always include `--remote-allow-origins=*`** — required for CDP WebSocket access
from scripts (cookie extraction, `Browser.setDownloadBehavior`, etc.). Without it
all WebSocket connections are rejected with 403.

```bash
ssh <user>@<satellite-host> 'bash -s' << 'EOF'
pkill -f 'remote-debugging-port=9222' 2>/dev/null || true
sleep 1
DISPLAY=:1 nohup google-chrome \
  --remote-debugging-port=9222 \
  --remote-allow-origins=* \
  --user-data-dir=/tmp/cdp-profile \
  --no-first-run \
  --no-default-browser-check \
  --disable-background-timer-throttling \
  > /tmp/chrome-cdp.log 2>&1 &
echo "Chrome PID: $!"
for i in 1 2 3 4 5 6 7 8 9 10; do
  sleep 1
  curl -sf http://localhost:9222/json/version >/dev/null 2>&1 \
    && echo "CDP ready after ${i}s" && break
  echo "Waiting... $i/10"
done
curl -s http://localhost:9222/json/version \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['Browser'])"
EOF
```

Typical output: `Chrome PID: 1033103` → `CDP ready after 1s` → `Chrome/147.0.7727.101`

**Notes**:
- `DISPLAY=:1` is Jupiter's cosmic-comp Wayland compositor — needed for a
  real (non-headless) Chrome session that can pass Cloudflare challenges
- `--user-data-dir=/tmp/cdp-profile` isolates the CDP session from the user
  profile; the directory is created automatically
- `nohup` keeps Chrome alive after the SSH session disconnects
- `/tmp/cdp-profile` does NOT survive Jupiter reboots — sessions (cookies)
  must be re-established after a reboot

---

## Bring Up web-fetcher

web-fetcher must be started with `BROWSER_CDP_URL` pointing at Chrome. Without
it, it launches its own headless Chromium and loses the residential-IP advantage.

```bash
ssh <user>@<satellite-host> 'bash -s' << 'EOF'
pkill -f 'scripts/web-fetcher' 2>/dev/null || true
fuser -k 8765/tcp 2>/dev/null || true
sleep 1
PYTHONPATH=/mnt/torus/projects/universal-llm-gateway/libs \
  BROWSER_CDP_URL=http://127.0.0.1:9222 \
  nohup ~/.venvs/universal/bin/python \
  /mnt/torus/projects/universal-llm-gateway/scripts/web-fetcher \
  --port 8765 \
  > /tmp/web-fetcher.log 2>&1 &
echo "web-fetcher PID: $!"
for i in 1 2 3 4 5 6; do
  sleep 1
  curl -sf http://localhost:8765/health >/dev/null 2>&1 && echo "ready after ${i}s" && break
done
curl -s http://localhost:8765/health
EOF
```

Expected: `{"status":"ok","concurrent_limit":3,"headless":null,"cdp_url_configured":true}`

> **Port conflict**: if port 8765 is already in use from a stale process, `fuser -k 8765/tcp`
> clears it before relaunching.

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

```bash
ssh <user>@<satellite-host> 'bash -s' << 'EOF'
pkill -f 'scripts/cdp-ask' 2>/dev/null || true
sleep 1
CORTEX_FILES_ROOT=/mnt/torus/mcp-data/files \
  nohup ~/.venvs/universal/bin/python \
  /mnt/torus/projects/universal-llm-gateway/scripts/cdp-ask \
  --port 8770 \
  > /tmp/cdp-ask.log 2>&1 &
echo "cdp-ask PID: $!"
for i in 1 2 3 4 5 6 7 8 9 10; do
  sleep 1
  curl -sf http://localhost:8770/health >/dev/null 2>&1 \
    && echo "cdp-ask ready after ${i}s" && break
done
curl -s http://localhost:8770/health
EOF
```

Expected: `{"status":"ok","harvest_root":"/mnt/torus/mcp-data/files","harvest_root_ok":true}`

> **Keep-alive**: cdp-ask uses `nohup` like web-fetcher — survives SSH disconnect.
> Re-run `--status` after disconnect to confirm `:8770/health` still responds.

---

## Full Bootstrap (when starting from scratch)

```bash
ssh <user>@<satellite-host> 'bash -s' << 'EOF'
set -e

# Kill stale processes
pkill -f 'remote-debugging-port=9222' 2>/dev/null || true
pkill -f 'scripts/web-fetcher' 2>/dev/null || true
fuser -k 8765/tcp 2>/dev/null || true
sleep 1

# Launch Chrome — --remote-allow-origins=* required for CDP WebSocket from scripts
DISPLAY=:1 nohup google-chrome \
  --remote-debugging-port=9222 \
  --remote-allow-origins=* \
  --user-data-dir=/tmp/cdp-profile \
  --no-first-run \
  --no-default-browser-check \
  --disable-background-timer-throttling \
  > /tmp/chrome-cdp.log 2>&1 &
echo "Chrome PID: $!"

# Wait for CDP (usually ready in 1s)
for i in 1 2 3 4 5 6 7 8 9 10; do
  sleep 1
  curl -sf http://localhost:9222/json/version >/dev/null 2>&1 \
    && echo "CDP ready after ${i}s" && break
  echo "Waiting for CDP... $i/10"
done

# Launch web-fetcher
PYTHONPATH=/mnt/torus/projects/universal-llm-gateway/libs \
  BROWSER_CDP_URL=http://127.0.0.1:9222 \
  nohup ~/.venvs/universal/bin/python \
  /mnt/torus/projects/universal-llm-gateway/scripts/web-fetcher \
  --port 8765 \
  > /tmp/web-fetcher.log 2>&1 &
echo "web-fetcher PID: $!"

# Verify both
sleep 2
echo "--- Chrome ---"
curl -s http://localhost:9222/json/version \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['Browser'])"
echo "--- web-fetcher ---"
curl -s http://localhost:8765/health
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
| `dispatch(tool="browse")` → connection error to `jupiter:8765` | web-fetcher not running | Start web-fetcher (see above) |
| `BrowserType.connect_over_cdp: connect ECONNREFUSED 127.0.0.1:9222` | Chrome not running | Launch Chrome (see above); restart web-fetcher after |
| `cdp_url_configured: true` but browser fetches fail | web-fetcher env var set but Chrome is down | The health field is misleading — check CDP directly with `curl http://localhost:9222/json/version` |
| Chrome crashes or becomes unresponsive | Memory pressure or JS crash | `pkill -f "remote-debugging-port=9222"` then re-launch |
| SSH session drops mid-launch | Network blip | Chrome survives via nohup; verify with `pgrep -a google-chrome` |
| Stale Chrome occupying port 9222 | Previous nohup Chrome still running | `pkill -f "remote-debugging-port=9222"` then re-launch |
| Stale process on port 8765 | Old web-fetcher still bound | `fuser -k 8765/tcp` then re-launch web-fetcher |
| CDP WebSocket → 403 Forbidden | Chrome launched without `--remote-allow-origins=*` | Restart Chrome with that flag (see launch commands) |

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
