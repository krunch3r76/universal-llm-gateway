---
trigger_match_terms: ["jupiter-browser-via-mcp", "jupiter_browser_via_mcp", "authed", "browser", "download", "scribd", "paywalled", "tooling-observability", "authenticated", "downloads", "pdfs", "cloudflare-protected"]
description: On authenticated browser downloads (Scribd, paywalled PDFs, Cloudflare). Read before browse against protected hosts — Jupiter Chrome CDP/Playwright.
---

# Jupiter Browser via MCP — Agent Guide

Bring up Chrome on Jupiter so the `browse` dispatch tool can fetch CF-protected
or JS-heavy pages using Jupiter's residential IP, and download authenticated
binary files (PDFs, documents) from sites where the user is logged in.

**Form fills / file uploads / multi-step SPA apply flows:** Use the `web-automation-discipline` skill — probe-first Playwright patterns, file-chooser hooks, seat routing. This skill covers transport (`browse`, `save_to`, bootstrap).

**Tested**: Chrome/147.0.7727.101 on Jupiter, April 2026.

## Launching (the normal human path)

```bash
scripts.local/start-jupiter-browser          # start if not running (idempotent)
scripts.local/start-jupiter-browser --force  # kill and restart all three services
scripts.local/start-jupiter-browser --status # check status without changing anything
```

Run from the repo root. The script SSHes to Jupiter, starts Chrome, web-fetcher,
and cdp-ask if needed, and prints final status. Takes ~5 seconds on a fresh
start, ~0.5 seconds if already running.

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

cdp-ask is the third leg of `scripts.local/start-jupiter-browser`. It serves
MCP `project_ask` (sealed CDP consults with cortex harvest). Requires Chrome
:9222 **and** `CORTEX_FILES_ROOT=/mnt/torus/mcp-data/files` on Jupiter.

### Production activation runbook

1. Jupiter: `git pull` in the ULG checkout (confirm `libs/cdp_ask/` present).
2. Hub: `scripts.local/start-jupiter-browser` (or `--status` first).
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

## Using the browse Tool

```python
# Auto mode — on Jupiter, plain HTTP always fails (SSL cert issue), so this
# always falls through to Chrome. Safe default.
dispatch(tool="browse", arguments='{"url":"https://carvana.com/cars"}')

# Force browser mode explicitly (preferred for CF-protected or JS-heavy sites)
dispatch(tool="browse", arguments='{"url":"https://carvana.com/cars","mode":"browser"}')

# With screenshot — useful for React/Vue SPAs where text extraction loses content
dispatch(tool="browse", arguments='{"url":"https://carvana.com/cars/4194613","mode":"browser","screenshot":true}')

# Narrow extraction via CSS selector (reduces token usage)
dispatch(tool="browse", arguments='{"url":"https://...","selector":"main","mode":"browser"}')
```

**When to use `browse` over `web_fetch`**:
- Site returns 403 or "Just a moment..." (Cloudflare challenge)
- React/Vue/Angular SPAs requiring JS rendering
- Need Jupiter's residential IP to avoid datacenter-IP blocks
- **Authenticated session required** (Uber, Scribd, court portals, etc.) — the
  live Chrome on Jupiter holds the cookies; `browser_navigate`/`browser_click`
  use a separate Firefox context and hit login walls
- **Click-through required before extraction** (download buttons, pagination,
  filter dropdowns) — see `actions` parameter below

### Parameter quick reference

| Param | Purpose | Section |
|---|---|---|
| `mode` | `auto`/`browser`/`http` | above |
| `selector` | CSS selector for narrow extraction | above |
| `screenshot`, `screenshot_format`, `screenshot_quality` | Visual capture | below |
| `screenshot_raw` | Embed as base64 in JSON (non-claude.ai clients) | below |
| `save_screenshot_to` | Persistent screenshot copy on Jupiter | below |
| `save_to` | Binary download path on Jupiter (w/ or w/o actions) | below |
| `wait_for` | Gate page readiness post-navigation (SPAs) | below |
| `actions` | Sequential server-side browser interactions | below |

**Note on `mode="auto"`**: On Jupiter, plain HTTP (httpx) always fails with an
SSL certificate error. `mode="auto"` silently falls back to Chrome in every
case. The returned JSON will show `"method":"browser"`. This is expected and fine
— you get browser-quality rendering regardless.

---

## Authenticated Downloads (`save_to`)

Use `save_to` when you need to **download a binary file** (PDF, DOCX, etc.)
from a site where the user is already logged in via Jupiter's Chrome. The
browser navigates using the live authenticated session, captures the download
event (or inline response bytes), and writes the file to a local path on Jupiter.

### When to use

- Site uses font DRM or JS rendering that makes text extraction garbled/useless
  (Scribd, PACER, court portals, gated document platforms)
- You need the actual binary file for RAG indexing, not extracted text
- The user says "I'm logged in" or "I have an account" for a gated resource

### Workflow

**Step 1 — Verify auth before downloading** (optional but recommended):

```python
# Fetch the page first to confirm content is accessible — look for actual
# document text, not a login wall or garbled font-DRM characters.
dispatch(tool="browse", arguments='{"url":"https://www.scribd.com/document/12345/Title","mode":"browser","selector":"main","max_chars":500}')
```

Signs of successful auth:
- Readable document text appears in `content`
- No "Sign in" / "Subscribe" paywall text

Signs of font DRM (content present but unusable as text):
- Garbled characters like `Jdtrjd\`kg Njrradts\`jd` — individual chars/tokens,
  not words — Scribd's glyph-scrambling for paywalled content
- Use `save_to` instead of trying to parse this text

**Step 2 — Download to `/tmp/` on Jupiter**:

```python
# save_to path must be on Jupiter (the web-fetcher host), not the local machine.
# /tmp/ is always writable. Use descriptive filenames.
dispatch(tool="browse", arguments='{
  "url": "https://www.scribd.com/document/12345/Title/download",
  "save_to": "/tmp/my-document.pdf",
  "mode": "browser"
}')
```

Expected response:
```json
{"saved_to": "/tmp/my-document.pdf", "size": 1139306, "url": "https://..."}
```

**Step 2a — ALWAYS verify the downloaded file is binary, not HTML**:

```bash
ssh <user>@<satellite-host> 'xxd -p -l 5 /tmp/my-document.pdf'
# PDF: 255044462d  → %PDF-   ✅
# HTML: 3c21444f43 → <!DOC   ❌  (web-fetcher saved the page, not the binary)
# HTML: 3c21646f63 → <!doc   ❌  (same — lowercase variant)
```

If you get HTML, the download URL is not serving a direct binary — it's rendering
a download page that requires a click. Use the CDP native download approach
(see next section) or `save_to` + `actions` to click the actual download button.

**Step 3 — SCP to local machine**:

```bash
scp <user>@<satellite-host>:/tmp/my-document.pdf $HOME/mcp-data/files/legal/.../my-document.pdf
```

The `/mnt/torus` NFS share is mounted on Jupiter but `$HOME/` is not —
always use `scp` to transfer files back.

**Step 4 — Index via RAG**:

```bash
curl -sf --unix-socket /tmp/universal-protocol/rag.sock http://localhost/index \
  -H 'Content-Type: application/json' \
  -d '{"path": "$HOME/mcp-data/files/legal/.../my-document.pdf"}'
```

Indexing PDFs with contextual extraction takes 2–5 minutes per file depending on
length and model load. Wait for the JSON response before declaring done.

### Parallel vs. sequential downloads

**Use parallel MCP calls — not sequential SSH loops.**

```python
# ✅ Correct — parallel dispatch calls; each completes in seconds
# Make all four CallMcpTool calls in the same response message
dispatch(tool="browse", arguments='{"url": "https://.../doc1/download", "save_to": "/tmp/doc1.pdf", "mode": "browser"}')
dispatch(tool="browse", arguments='{"url": "https://.../doc2/download", "save_to": "/tmp/doc2.pdf", "mode": "browser"}')
dispatch(tool="browse", arguments='{"url": "https://.../doc3/download", "save_to": "/tmp/doc3.pdf", "mode": "browser"}')
```

```python
# ❌ Wrong — Python loop over SSH with urlopen(timeout=120) will timeout for >3-4 files
for url, path in docs:
    urllib.request.urlopen(Request("http://localhost:8765/fetch", ...), timeout=120)
    # Each download takes 20-40s; 5 sequential = 100-200s → SSH timeout exceeded
```

### Scribd-specific download URL pattern

Scribd documents use the path `/download` appended to the document URL:

```
https://www.scribd.com/document/{ID}/{Title}/download
```

> **Warning**: Scribd's `/download` URL may render an intermediate download page
> (with a JS challenge) rather than serving the binary directly. If `save_to` returns
> an HTML file (verify with `xxd`), use the CDP native download approach below.

### Full example (Scribd PDF → RAG)

```python
# 1. Download to Jupiter /tmp/ via parallel dispatch calls
dispatch(tool="browse", arguments='{
  "url": "https://www.scribd.com/document/490414881/Petition-to-Compel-Accounting-2018/download",
  "save_to": "/tmp/petition-compel-accounting-2018-scribd.pdf",
  "mode": "browser"
}')
# → {"saved_to": "/tmp/petition-compel-accounting-2018-scribd.pdf", "size": 1021435}

# 2. Verify it's actually a PDF (not HTML)
# ssh <user>@<satellite-host> 'xxd -p -l 5 /tmp/petition-compel-accounting-2018-scribd.pdf'
# Expected: 255044462d  → %PDF-

# 3. SCP to local mcp-data
# scp <user>@<satellite-host>:/tmp/petition-compel-accounting-2018-scribd.pdf \
#     $HOME/mcp-data/files/legal/writing-samples/petitions/

# 4. Index
# curl -sf --unix-socket /tmp/universal-protocol/rag.sock http://localhost/index \
#   -H 'Content-Type: application/json' \
#   -d '{"path": "$HOME/mcp-data/files/legal/writing-samples/petitions/petition-compel-accounting-2018-scribd.pdf"}'
```

---

## CDP Native Downloads (`Browser.setDownloadBehavior`)

This is the **preferred approach for authenticated binary downloads** from sites
where the download URL serves an HTML page with a JS challenge rather than a
direct binary. Chrome handles the challenge natively and saves the file to disk.

Use this when:
- `save_to` returns HTML instead of a PDF (detected via `xxd -p -l 5`)
- The download is triggered by a JS event that Playwright's `expect_download()` misses
- You want Chrome to use its native download manager without any Playwright layer

### Prerequisites

Chrome must be running with `--remote-allow-origins=*` (see launch commands above).
Without this flag, the CDP WebSocket connection returns 403.

### Workflow

```bash
ssh <user>@<satellite-host> 'bash -s' << 'EOF'
python3 - << 'PYEOF'
import json, urllib.request, websocket, time, os, glob

# 1. Get CDP WebSocket URL for the active tab
tabs = json.loads(urllib.request.urlopen("http://localhost:9222/json", timeout=5).read())
ws_url = next(t["webSocketDebuggerUrl"] for t in tabs if t.get("type") == "page")

ws = websocket.create_connection(ws_url, timeout=10,
    header=["Origin: http://localhost:9222"])

# 2. Tell Chrome to save all downloads to /tmp/
ws.send(json.dumps({"id": 1, "method": "Browser.setDownloadBehavior",
    "params": {"behavior": "allow", "downloadPath": "/tmp/"}}))
ws.recv()  # ack

# 3. Navigate to the download URL — Chrome handles JS challenge, triggers download
before = set(glob.glob("/tmp/*.pdf"))
ws.send(json.dumps({"id": 2, "method": "Page.navigate",
    "params": {"url": "https://www.scribd.com/document/243103801/Sample-Notice-of-Error-for-Dual-Tracking-Violations/download"}}))
ws.recv()  # nav ack

# 4. Wait for download to appear in /tmp/ (poll with timeout)
for i in range(60):  # 60s max
    time.sleep(1)
    new_files = set(glob.glob("/tmp/*.pdf")) - before
    if new_files:
        fpath = list(new_files)[0]
        size = os.path.getsize(fpath)
        print(f"Downloaded: {fpath} ({size} bytes)")
        break
else:
    print("TIMEOUT: no new PDF appeared in /tmp/")

ws.close()
PYEOF
EOF
```

### Renaming Chrome's auto-generated filename

Chrome saves the file with its own generated name (e.g., `Sample Notice of Error...pdf`).
After download, rename to your convention:

```bash
ssh <user>@<satellite-host> '
  newest=$(ls -t /tmp/*.pdf | head -1)
  mv "$newest" "/tmp/nclc-noe-dual-tracking-violations.pdf"
  echo "Renamed to: /tmp/nclc-noe-dual-tracking-violations.pdf"
'
```

### Batch CDP downloads (multiple files)

For multiple documents, re-use the same WebSocket connection and call `Page.navigate`
for each URL sequentially, waiting for the new file to appear before navigating to the next:

```bash
ssh <user>@<satellite-host> 'bash -s' << 'EOF'
python3 - << 'PYEOF'
import json, urllib.request, websocket, time, os, glob, shutil

docs = [
    ("/tmp/nclc-noe-dual-tracking.pdf",   "https://www.scribd.com/document/243103801/.../download"),
    ("/tmp/castillo-v-nationstar.pdf",     "https://www.scribd.com/document/369232086/.../download"),
    ("/tmp/lucero-v-cenlar.pdf",           "https://www.scribd.com/document/474749830/.../download"),
]

tabs = json.loads(urllib.request.urlopen("http://localhost:9222/json", timeout=5).read())
ws_url = next(t["webSocketDebuggerUrl"] for t in tabs if t.get("type") == "page")
ws = websocket.create_connection(ws_url, timeout=10, header=["Origin: http://localhost:9222"])

ws.send(json.dumps({"id": 1, "method": "Browser.setDownloadBehavior",
    "params": {"behavior": "allow", "downloadPath": "/tmp/"}}))
ws.recv()

for i, (dest, url) in enumerate(docs):
    before = set(glob.glob("/tmp/*.pdf"))
    ws.send(json.dumps({"id": i+10, "method": "Page.navigate", "params": {"url": url}}))
    ws.recv()
    for _ in range(60):
        time.sleep(1)
        new = set(glob.glob("/tmp/*.pdf")) - before
        if new:
            src = sorted(new, key=os.path.getmtime)[-1]
            shutil.move(src, dest)
            print(f"OK  {dest}  ({os.path.getsize(dest)} bytes)")
            break
    else:
        print(f"TIMEOUT  {dest}")

ws.close()
PYEOF
EOF
```

---

## Waiting for Hydration (`wait_for`)

React/Vue/Angular SPAs fire `domcontentloaded` before the framework has
hydrated — `browse` may see a near-empty shell. Use `wait_for` to gate the
extraction on page readiness:

```python
# Wait for a specific element to render
dispatch(tool="browse", arguments='{
  "url": "https://drivers.uber.com/earnings/statements",
  "mode": "browser",
  "wait_for": {"type": "selector", "value": "[data-testid=statements-list]", "timeout_ms": 15000}
}')

# Wait for network to go quiet (best effort — doesn't work well with long polls)
dispatch(tool="browse", arguments='{
  "url": "https://app.example.com/dashboard",
  "mode": "browser",
  "wait_for": {"type": "networkidle", "timeout_ms": 15000}
}')

# Fixed delay (escape hatch for framework-specific startup)
dispatch(tool="browse", arguments='{
  "url": "https://...",
  "mode": "browser",
  "wait_for": {"type": "timeout_ms", "value": 3000}
}')
```

Supported `type` values:

| type | value | Semantics |
|---|---|---|
| `selector` | CSS selector string | Wait for element to be visible (default state) |
| `networkidle` | — | Wait for 500ms of network quiet (uses `timeout_ms` as cap) |
| `timeout_ms` | ms (via `value`) | Fixed sleep |

Applied **once** after initial navigation, before any `actions` run. If the
wait fails (timeout, selector never appears), the response includes
`action_failure: {error, failed_at: -1, last_url}` and the HTML is still
returned so you can diagnose what the page looked like when the wait timed out.

---

## Interactive Actions (`actions`)

For multi-step flows (click a filter, fill a search, press Enter) that have to
happen before the content you want is visible, pass an `actions` array. Each
action executes server-side in order on a single page — no round-trips per step.

```python
dispatch(tool="browse", arguments='{
  "url": "https://search.example.com",
  "mode": "browser",
  "wait_for": {"type": "selector", "value": "#q", "timeout_ms": 10000},
  "actions": [
    {"type": "fill", "selector": "#q", "value": "structural engineering"},
    {"type": "press", "key": "Enter"},
    {"type": "wait_for_selector", "selector": ".results-list", "timeout_ms": 10000}
  ],
  "selector": ".results-list"
}')
```

Supported action types:

| type | required fields | optional fields |
|---|---|---|
| `click` | `selector` | `button` (left/right/middle), `click_count`, `timeout_ms` |
| `fill` | `selector`, `value` | `timeout_ms` |
| `press` | `key` | `selector` (targets an element), `timeout_ms` |
| `select_option` | `selector`, one of `value`/`label`/`index` | `timeout_ms` |
| `hover` | `selector` | `timeout_ms` |
| `wait_for_selector` | `selector` | `state` (visible/attached/hidden), `timeout_ms` |
| `wait_for_timeout` | `timeout_ms` | — |
| `wait_for_networkidle` | — | `timeout_ms` |

On action failure, the response includes:

```json
{
  "action_failure": {
    "error": "<exception message>",
    "failed_at": 2,
    "last_url": "https://..."
  },
  "content": "<HTML at point of failure>",
  ...
}
```

`failed_at` is the zero-based index of the action that failed, or `-1` when
the preceding `wait_for` failed.

---

## Click-Triggered Downloads (`save_to` + `actions`)

When the PDF / binary isn't behind a direct URL but behind a "Download" button
that triggers a browser download event, combine `save_to` and `actions`. The
web-fetcher wraps the full action sequence in `page.expect_download()` and
captures whichever action fires the download.

### Worked example — Uber weekly statements

```python
# 1. Open the page and confirm the statements list is hydrated.
dispatch(tool="browse", arguments='{
  "url": "https://drivers.uber.com/earnings/statements",
  "mode": "browser",
  "wait_for": {"type": "selector", "value": "[data-testid=statement-row], a[href*=statement]", "timeout_ms": 20000},
  "screenshot": true,
  "max_chars": 4000
}')

# 2. Click the Download button, capturing the PDF.
dispatch(tool="browse", arguments='{
  "url": "https://drivers.uber.com/earnings/statements",
  "mode": "browser",
  "wait_for": {"type": "selector", "value": "[data-testid=statement-row]", "timeout_ms": 20000},
  "actions": [
    {"type": "click", "selector": "[data-testid=statement-row]:first-of-type button[aria-label*=Download i]", "timeout_ms": 15000}
  ],
  "save_to": "/tmp/uber-statement-latest.pdf"
}')
```

> **If `save_to` + `actions` returns HTML**: the click may not be triggering a
> download event detectable by Playwright. Switch to the CDP native download
> approach — click the button via CDP actions, then poll `/tmp/` for the new file.

---

## Screenshots Visible to Agents (`screenshot_path`)

When `screenshot=True`, the `browse` MCP wrapper auto-decodes the returned
base64 and writes it into the MCP container's shared image directory
(`MCP_SHARED_IMAGE_DIR`, default `/data/files/.shared-images/`). Two new
fields appear in the response:

| Field | Meaning |
|---|---|
| `screenshot_path` | Container-local path — feed directly to `view_image` |
| `screenshot_host_path` | Where the file actually lives on the host (usually the same) |

Agent workflow:

```python
r = dispatch(tool="browse", arguments='{"url":"https://...","mode":"browser","screenshot":true}')
# r["result"]["screenshot_path"] → e.g. "/data/files/.shared-images/browse-1745020000-abc123.png"
dispatch(tool="view_image", arguments=f'{{"path":"{r["result"]["screenshot_path"]}"}}')
```

If `screenshot_path` is missing from the response or `view_image` returns "file not found":
- Copy the file out of the MCP container: `docker cp $(docker ps --filter name=mcp -q | head -1):/data/files/.shared-images/FILENAME /tmp/FILENAME`
- Then read it locally with the Read tool

---

## Text Extraction vs. Download — Decision Guide

| Situation | Approach |
|---|---|
| Page renders readable text via JS (SPA, no DRM) | `browse` + `wait_for` |
| PDF embedded in page, text extractable via DOM | `browse` with `selector` |
| Content hidden behind a filter/search form | `browse` + `actions` (fill + click) |
| Download button triggers a browser download event | `browse` + `actions` + `save_to` |
| Direct URL to a binary (Scribd-style `/download`, when working) | `browse` with `save_to` (no actions) — **verify with xxd** |
| `/download` URL returns HTML (JS challenge page) | CDP `Browser.setDownloadBehavior` approach |
| Font DRM / garbled characters in extracted text | `save_to` → verify → scp → index as PDF |
| Binary file behind authenticated download button | `save_to` + `actions` → verify → scp |
| Cloudflare-protected but text-based | `browse` with `mode:"browser"` |
| Need the rendered page visually (chart, map, layout) | `browse` with `screenshot=true` → `view_image(screenshot_path)` |
| Public PDF (no auth, no DRM, no JS challenge) | `curl -sL <url> -o /path/file.pdf` — then **verify with xxd** |

---

## Failure Modes and Fixes

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
| `save_to` → `Permission denied: '$HOME'` | Jupiter can't write to local `$HOME/` | Use `/tmp/` on Jupiter, then `scp` back |
| `save_to` → `size: 3000` (suspiciously small) | Got HTML error page instead of file | Check auth: fetch the page first and confirm content is readable |
| `save_to` → `size: 1MB+` but `xxd -p -l 5` shows `3c21...` (HTML) | Download URL rendered a JS-challenge page; web-fetcher saved the HTML, not the binary | Use CDP `Browser.setDownloadBehavior` approach |
| `save_to` succeeds but PDF is blank/corrupted | Download URL requires a click event, not direct navigation | Use `save_to` + `actions=[{click}]` or CDP native download |
| Sequential Python SSH download loop times out | SSH connection timeout exceeded with >3-4 sequential 20-40s downloads | Use parallel `dispatch` MCP calls in a single agent message instead |
| CDP WebSocket → 403 Forbidden | Chrome launched without `--remote-allow-origins=*` | Restart Chrome with that flag (see launch commands) |
| Extracted text is garbled (`Jdtrjd\`kg...`) | Font DRM — Scribd encodes glyphs via custom CSS font | Don't parse; use CDP native download or `save_to` to get the actual PDF |
| `action_failure.error: "Timeout ... waiting for selector"` | Selector never resolved | Run without `actions` first and inspect the HTML / screenshot to pick a real selector |
| `action_failure.failed_at: -1` | Top-level `wait_for` timed out | The page didn't reach the expected state — try `networkidle` or a broader selector |
| `screenshot_path` missing from response | `screenshot=True` but the shared-image dir isn't writable | Copy via `docker cp` as fallback (see Screenshots section) |

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

## Related Files

| Path | Purpose |
|---|---|
| `scripts.local/start-jupiter-browser` | **Launch script** — start/stop/status Chrome + web-fetcher + cdp-ask |
| `scripts/cdp-ask` | cdp-ask satellite entry point (port 8770) |
| `libs/cdp_ask/` | cdp-ask FastAPI app + CDP project-ask runner |
| `services/mcp-server/tools/project_ask.py` | MCP `project_ask` relay to PROJECT_ASK_URL |
| `libs/web_fetcher/` | web-fetcher FastAPI app + Playwright browser module |
| `libs/web_fetcher/app.py` | FastAPI surface — `FetchRequest` schema, routing logic |
| `libs/web_fetcher/browser.py` | `fetch_with_browser` (text + actions + downloads) + `download_with_browser` (direct-URL) |
| `libs/web_fetcher/actions.py` | Action primitives (`click`/`fill`/`press`/…) + `wait_for` dispatcher |
| `libs/web_fetcher/cloudflare.py` | CF challenge detection + Turnstile click handling |
| `scripts/web-fetcher` | Low-level web-fetcher entry point (called by `start-jupiter-browser`) |
| `services/mcp-server/tools/browse.py` | `browse` MCP tool — relay + shared-image copy for `screenshot_path` |
| `services/mcp-server/tools/web.py` | `web_search` + `web_fetch` (HTTP-only) |
| `docker/compose/mcp-server.yml` | Declares `WEB_FETCHER_URL` + `MCP_SHARED_IMAGE_DIR` env vars |
| `~/.gateway/mcp.yaml` | Sets `WEB_FETCHER_URL: "http://jupiter:8765"` and `PROJECT_ASK_URL: "http://jupiter:8770"` |

---

## Source

Reconstructed and tested from [Carvana Jupiter browsing session](cursor-2026-04-11-1934).
`save_to` download capability added in [Scribd PDF ingest session](cursor-2026-04-12-0830).
`wait_for` + `actions` + click-triggered downloads + `screenshot_path`
auto-copy added in [Jupiter browser gap closure session](cursor-2026-04-18-0500)
(thread 603).
CDP `Browser.setDownloadBehavior` + `--remote-allow-origins=*` + parallel download
patterns + xxd verification + port-conflict fix added in
[§1024.35 NOE corpus ingest session](cursor-2026-04-27-2253).
Distinct **AWS CloudFront WAF JS-challenge** failure mode (vs. standard Cloudflare TLS
fingerprint), `user-vortex-web_fetch` httpx-only behavior, response-store flagging
for large-page retrieval, and `cursor-ide-browser-browser_get_content` no-pagination
limitation added in [Prop 19 legal corpus ingest session](cursor-2026-05-03-1730)
(thread 879). See § May 2026 below.

## May 2026 — WAF, Tool Boundaries, and Large-Page Retrieval

Four findings from the BOE-19-P / `legal_prop19` corpus ingest (CourtListener,
thread 879). Each tightens the decision tree above.

### 1. AWS CloudFront WAF JS-challenge — distinct from Cloudflare TLS block

Assertion #1963 (the canonical `curl_cffi` impersonate=chrome recipe) covers
**Cloudflare TLS-fingerprint** blocks. CourtListener as of May 2026 serves a
DIFFERENT challenge: AWS CloudFront WAF with JS execution requirement.

**Signature**:

| Signal | Value |
|---|---|
| Status code | `202 Accepted` (not 403) |
| Response header | `x-amzn-waf-action: challenge` |
| Response body | HTML containing `<noscript>JavaScript is disabled` |
| `curl_cffi` impersonate=chrome | **Fails** — TLS fingerprint is fine; the block is at the JS-execution layer |

**Decision rule**: any 202 + `x-amzn-waf-action: challenge` → skip `curl_cffi`
entirely, go straight to `dispatch(tool="browse", mode="browser", wait_for=...)`.
A real JS-executing browser is the only solver.

```python
# Working pattern for CourtListener and similar AWS-WAF-protected sites
dispatch(tool="browse", arguments='{
  "url": "https://www.courtlistener.com/opinion/4397103/williams-fickett-v-cnty-of-fresno/",
  "mode": "browser",
  "wait_for": {"type": "selector", "value": "article, .opinion, h1", "timeout_ms": 25000},
  "max_chars": 200000
}')
```

The `wait_for` selector is REQUIRED — without it, `browse` may return the
challenge interstitial ("JavaScript is disabled" body) before Chrome has
resolved the WAF check and rendered the actual opinion. 25s is a safe ceiling;
typical resolution is 3-8s.

### 2. `user-vortex-web_fetch` is httpx-only — does NOT fall through to Jupiter

Footgun: the **top-level** `user-vortex-web_fetch` MCP tool and
`dispatch(tool="browse", mode="browser")` look interchangeable but are two
distinct paths.

| Tool | Transport | Behavior on JS-protected page |
|---|---|---|
| `user-vortex-web_fetch` (top-level) | httpx in MCP container | Returns truncated HTML + "trafilatura extraction failed" — silent failure dressed as success |
| `dispatch(tool="browse", mode="browser")` | Jupiter Chrome via CDP | Bypasses WAF/JS challenges |

`web_fetch` does **not** transparently fall through to Jupiter Chrome. Its
failure mode (extraction-failed message + raw HTML body) reads like a content
problem rather than a transport problem, and is easy to misdiagnose as "page
is malformed" when the actual issue is the WAF.

**Decision rule**: any site where `web_fetch` returns trafilatura-extraction-failed,
or where the returned content contains "JavaScript is disabled" / "Just a moment"
/ login wall text → switch to `dispatch(tool="browse", mode="browser")` rather
than retry `web_fetch` with different parameters.

### 3. Response-store flagging is the canonical large-page retrieval pattern

When `dispatch(tool="browse", max_chars=N)` returns a payload >128KB, the MCP
relay auto-flags into `rs_XXXXX` rather than returning inline. This is
**expected and correct** for full court opinions, long articles, etc.

```
response → "Large dispatch payload flagged. Size: 156.3KB over 128.0KB threshold.
            Stored as: rs_a1b2c3 (expires in 10 min)."
→ retrieve(id="rs_a1b2c3")
→ writes JSON to $HOME/.cursor/projects/.../agent-tools/<uuid>.txt
→ Read that file → it's the full result JSON; parse with python json.load
```

The natural agent reflex is to **lower** `max_chars` on the next call to fit
inline. **This silently truncates the content.** The right move is to keep
`max_chars` high (200000+) and use `retrieve` → file → parse.

**Decision rule**: for content where completeness matters (legal opinions for
cite-verification, full articles for ingestion, anything where "verbatim"
matters) — set `max_chars: 200000` and accept the response-store hop. Only
lower `max_chars` when you genuinely want a preview.

### 4. `cursor-ide-browser-browser_get_content` has no pagination

The Cursor IDE-browser MCP server's `browser_get_content` accepts NO arguments
— no `offset`, `limit`, `format`, `max_chars`, `page`, `start_offset`,
`truncate_at`. It returns whatever it returns and that's it. For long pages,
the tail is unreachable through this path.

**Decision rule**: long page (>10K chars expected) on a JS-protected site →
**always** `dispatch(tool="browse", max_chars=N)`, **never**
`cursor-ide-browser-browser_navigate` + `browser_get_content`. The IDE-browser
path is fine for short interactive verifications (form fills, screenshots,
small extractions); it is not fine for content-bearing extraction.

### Updated Decision Guide additions

Add these rows to the Text Extraction vs. Download table above:

| Situation | Approach |
|---|---|
| Site returns 202 + `x-amzn-waf-action: challenge` | `dispatch(tool="browse", mode="browser", wait_for=<content selector>)` — **never** `curl_cffi` |
| `user-vortex-web_fetch` returns trafilatura-extraction-failed | Switch to `dispatch(tool="browse", mode="browser")` — `web_fetch` does not fall through |
| Need full long-form content (court opinion, article, doc) | `dispatch(tool="browse", max_chars=200000)` → `retrieve(id="rs_*")` → read file → parse JSON |
| Long page (>10K chars) on JS-protected site | `dispatch(tool="browse")`, **not** `cursor-ide-browser-browser_get_content` (no pagination) |

### Updated Failure Modes additions

Add these rows to the Failure Modes table above:

| Symptom | Cause | Fix |
|---|---|---|
| Site returns HTTP 202 + `x-amzn-waf-action: challenge` header | AWS CloudFront WAF JS-challenge (NOT Cloudflare TLS) | `dispatch(tool="browse", mode="browser", wait_for={selector})` — `curl_cffi` cannot solve this |
| `dispatch(tool="browse")` returns body containing "JavaScript is disabled" | WAF challenge served instead of content; `wait_for` missing or timeout too short | Add `wait_for: {type: selector, value: <content selector>, timeout_ms: 25000}` |
| `user-vortex-web_fetch` returns trafilatura-extraction-failed | `web_fetch` is httpx-only; site requires JS execution | Switch to `dispatch(tool="browse", mode="browser")`; do NOT retry `web_fetch` |
| Response carries `"Stored as: rs_XXXXX"` instead of inline content | Payload exceeded 128KB threshold (expected for long content) | `retrieve(id="rs_XXXXX")` → read written file → parse JSON. Do NOT lower `max_chars` to fit inline (truncates) |
| `cursor-ide-browser-browser_get_content` returns truncated content with no way to get the tail | Tool accepts no offset/limit/page args — no pagination exists | Use `dispatch(tool="browse", max_chars=N)` instead for any long-form extraction |
| Playwright form/upload on authenticated site fails or selectors wrong | Used wrong seat (IDE browser) or guessed selectors | Use `web-automation-discipline` — Jupiter CDP + probe + `expect_file_chooser` |
