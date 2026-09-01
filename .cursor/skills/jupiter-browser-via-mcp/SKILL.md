---
trigger_match_terms: ["jupiter-browser-via-mcp", "jupiter_browser_via_mcp", "authed", "browser", "download", "scribd", "paywalled", "tooling-observability", "authenticated", "downloads", "pdfs", "cloudflare-protected"]
description: On authenticated browser downloads (Scribd, paywalled PDFs, Cloudflare). Read before browse against protected hosts — Jupiter Chrome CDP/Playwright.
---

# Jupiter Browser via MCP — Agent Guide

Use `browse` (backed by Chrome on Jupiter) to fetch CF-protected or JS-heavy
pages using Jupiter's residential IP, and download authenticated binary files
(PDFs, documents) from sites where the user is logged in.

**Infra down / never started?** Read `jupiter-browser-bringup` first — Chrome /
web-fetcher / cdp-ask launch, status check, and bootstrap live there, not here.

**Form fills / file uploads / multi-step SPA apply flows:** Use the `web-automation-discipline` skill — probe-first Playwright patterns, file-chooser hooks, seat routing. This skill covers transport (`browse`, `save_to`, bootstrap).

**Tested**: Chrome/147.0.7727.101 on Jupiter, April 2026.

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
- Issuer / bank / any authenticated host after login — statement PDF, invoice, court filing. Playwright `expect_download` timeout is **not** “PDF unreachable” (9784 BofA class, 2026-08-30)

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

Chrome must be running with `--remote-allow-origins=*` (see `jupiter-browser-bringup`
launch commands). Without this flag, the CDP WebSocket connection returns 403.

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
| Site returns 202 + `x-amzn-waf-action: challenge` | `dispatch(tool="browse", mode="browser", wait_for=<content selector>)` — **never** `curl_cffi`. Detail: `browse-waf-and-pagination-gotchas` |
| `user-vortex-web_fetch` returns trafilatura-extraction-failed | Switch to `dispatch(tool="browse", mode="browser")` — `web_fetch` does not fall through |
| Need full long-form content (court opinion, article, doc) | `dispatch(tool="browse", max_chars=200000)` → `retrieve(id="rs_*")` → read file → parse JSON |
| Long page (>10K chars) on JS-protected site | `dispatch(tool="browse")`, **not** `cursor-ide-browser-browser_get_content` (no pagination) |

---

## Failure Modes and Fixes

| Symptom | Cause | Fix |
|---|---|---|
| `save_to` → `Permission denied: '$HOME'` | Jupiter can't write to local `$HOME/` | Use `/tmp/` on Jupiter, then `scp` back |
| `save_to` → `size: 3000` (suspiciously small) | Got HTML error page instead of file | Check auth: fetch the page first and confirm content is readable |
| `save_to` → `size: 1MB+` but `xxd -p -l 5` shows `3c21...` (HTML) | Download URL rendered a JS-challenge page; web-fetcher saved the HTML, not the binary | Use CDP `Browser.setDownloadBehavior` approach |
| `save_to` succeeds but PDF is blank/corrupted | Download URL requires a click event, not direct navigation | Use `save_to` + `actions=[{click}]` or CDP native download |
| Playwright `expect_download` / `wait_for_event("download")` times out | Click opened a viewer, blob URL, or delayed attachment — not a Playwright download | **Class (any site):** `dispatch(tool="browse")` + `save_to` on the signed-in Jupiter tab; if HTML, `save_to`+click `actions` or CDP `Browser.setDownloadBehavior`. `xxd` for `%PDF-`. Do not park “PDF timed out” as a standing gap |
| Sequential Python SSH download loop times out | SSH connection timeout exceeded with >3-4 sequential 20-40s downloads | Use parallel `dispatch` MCP calls in a single agent message instead |
| CDP WebSocket → 403 Forbidden | Chrome launched without `--remote-allow-origins=*` | Restart Chrome with that flag — see `jupiter-browser-bringup` |
| Extracted text is garbled (`Jdtrjd\`kg...`) | Font DRM — Scribd encodes glyphs via custom CSS font | Don't parse; use CDP native download or `save_to` to get the actual PDF |
| `action_failure.error: "Timeout ... waiting for selector"` | Selector never resolved | Run without `actions` first and inspect the HTML / screenshot to pick a real selector |
| `action_failure.failed_at: -1` | Top-level `wait_for` timed out | The page didn't reach the expected state — try `networkidle` or a broader selector |
| `screenshot_path` missing from response | `screenshot=True` but the shared-image dir isn't writable | Copy via `docker cp` as fallback (see Screenshots section) |
| Site returns HTTP 202 + `x-amzn-waf-action: challenge` header | AWS CloudFront WAF JS-challenge (NOT Cloudflare TLS) | Detail + working pattern: `browse-waf-and-pagination-gotchas` |
| `user-vortex-web_fetch` returns trafilatura-extraction-failed | `web_fetch` is httpx-only; site requires JS execution | Detail: `browse-waf-and-pagination-gotchas` |
| Response carries `"Stored as: rs_XXXXX"` instead of inline content | Payload exceeded 128KB threshold (expected for long content) | `retrieve(id="rs_XXXXX")` → read written file → parse JSON. Do NOT lower `max_chars` to fit inline (truncates) |
| `cursor-ide-browser-browser_get_content` returns truncated content with no way to get the tail | Tool accepts no offset/limit/page args — no pagination exists | Use `dispatch(tool="browse", max_chars=N)` instead for any long-form extraction |
| Playwright form/upload on authenticated site fails or selectors wrong | Used wrong seat (IDE browser) or guessed selectors | Use `web-automation-discipline` — Jupiter CDP + probe + `expect_file_chooser` |

For Jupiter infra failures (Chrome/web-fetcher/cdp-ask not running, port
conflicts), read `jupiter-browser-bringup` instead.

---

## Related Files

| Path | Purpose |
|---|---|
| `libs/web_fetcher/browser.py` | `fetch_with_browser` (text + actions + downloads) + `download_with_browser` (direct-URL) |
| `libs/web_fetcher/actions.py` | Action primitives (`click`/`fill`/`press`/…) + `wait_for` dispatcher |
| `libs/web_fetcher/cloudflare.py` | CF challenge detection + Turnstile click handling |
| `services/mcp-server/tools/browse.py` | `browse` MCP tool — relay + shared-image copy for `screenshot_path` |
| `services/mcp-server/tools/web.py` | `web_search` + `web_fetch` (HTTP-only) |

Infra/bring-up files (launch script, web-fetcher/cdp-ask entry points, mcp.yaml) live
in `jupiter-browser-bringup` § Related Files.

## Related skills

`jupiter-browser-bringup` (Chrome/web-fetcher/cdp-ask infra, launch, status) ·
`browse-waf-and-pagination-gotchas` (AWS WAF, web_fetch footgun, response-store,
pagination) · `web-automation-discipline` (interactive form automation on top of
this transport).

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
limitation found in [Prop 19 legal corpus ingest session](cursor-2026-05-03-1730)
(thread 879) — split out into `browse-waf-and-pagination-gotchas` (2026-09-01,
agent-bus:9853 G5). Bring-up/infra content split into `jupiter-browser-bringup`
same date.
