---
trigger_match_terms: ["x-amzn-waf-action", "AWS CloudFront WAF", "202 Accepted challenge", "JavaScript is disabled", "trafilatura-extraction-failed", "response-store", "rs_", "browser_get_content pagination", "courtlistener waf"]
description: AWS CloudFront WAF JS-challenge (x-amzn-waf-action), web_fetch httpx-only footgun, response-store large-page retrieval, and browser_get_content pagination limits for dispatch(tool=browse).
---

# Browse tool — WAF and pagination gotchas

Four narrow diagnosis patterns for `dispatch(tool="browse")` / `web_fetch` /
`cursor-ide-browser` that are easy to misdiagnose as a content problem when
they are actually a transport-boundary problem. For general `browse` usage
(downloads, actions, screenshots) read `jupiter-browser-via-mcp`; for infra
bring-up read `jupiter-browser-bringup`.

Findings from the BOE-19-P / `legal_prop19` corpus ingest (CourtListener,
thread 879, May 2026). Each tightens the decision tree in `jupiter-browser-via-mcp`
§ Text Extraction vs. Download — Decision Guide.

## 1. AWS CloudFront WAF JS-challenge — distinct from Cloudflare TLS block

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

## 2. `user-vortex-web_fetch` is httpx-only — does NOT fall through to Jupiter

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

## 3. Response-store flagging is the canonical large-page retrieval pattern

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

## 4. `cursor-ide-browser-browser_get_content` has no pagination

The Cursor IDE-browser MCP server's `browser_get_content` accepts NO arguments
— no `offset`, `limit`, `format`, `max_chars`, `page`, `start_offset`,
`truncate_at`. It returns whatever it returns and that's it. For long pages,
the tail is unreachable through this path.

**Decision rule**: long page (>10K chars expected) on a JS-protected site →
**always** `dispatch(tool="browse", max_chars=N)`, **never**
`cursor-ide-browser-browser_navigate` + `browser_get_content`. The IDE-browser
path is fine for short interactive verifications (form fills, screenshots,
small extractions); it is not fine for content-bearing extraction.

## Decision guide additions (mirrors `jupiter-browser-via-mcp` § Decision Guide)

| Situation | Approach |
|---|---|
| Site returns 202 + `x-amzn-waf-action: challenge` | `dispatch(tool="browse", mode="browser", wait_for=<content selector>)` — **never** `curl_cffi` |
| `user-vortex-web_fetch` returns trafilatura-extraction-failed | Switch to `dispatch(tool="browse", mode="browser")` — `web_fetch` does not fall through |
| Need full long-form content (court opinion, article, doc) | `dispatch(tool="browse", max_chars=200000)` → `retrieve(id="rs_*")` → read file → parse JSON |
| Long page (>10K chars) on JS-protected site | `dispatch(tool="browse")`, **not** `cursor-ide-browser-browser_get_content` (no pagination) |

## Failure modes (mirrors `jupiter-browser-via-mcp` § Failure Modes)

| Symptom | Cause | Fix |
|---|---|---|
| Site returns HTTP 202 + `x-amzn-waf-action: challenge` header | AWS CloudFront WAF JS-challenge (NOT Cloudflare TLS) | `dispatch(tool="browse", mode="browser", wait_for={selector})` — `curl_cffi` cannot solve this |
| `dispatch(tool="browse")` returns body containing "JavaScript is disabled" | WAF challenge served instead of content; `wait_for` missing or timeout too short | Add `wait_for: {type: selector, value: <content selector>, timeout_ms: 25000}` |
| `user-vortex-web_fetch` returns trafilatura-extraction-failed | `web_fetch` is httpx-only; site requires JS execution | Switch to `dispatch(tool="browse", mode="browser")`; do NOT retry `web_fetch` |
| Response carries `"Stored as: rs_XXXXX"` instead of inline content | Payload exceeded 128KB threshold (expected for long content) | `retrieve(id="rs_XXXXX")` → read written file → parse JSON. Do NOT lower `max_chars` to fit inline (truncates) |
| `cursor-ide-browser-browser_get_content` returns truncated content with no way to get the tail | Tool accepts no offset/limit/page args — no pagination exists | Use `dispatch(tool="browse", max_chars=N)` instead for any long-form extraction |

## Related skills

`jupiter-browser-via-mcp` (browse usage, downloads, actions) · `jupiter-browser-bringup`
(Chrome/web-fetcher/cdp-ask infra) · `web-automation-discipline` (interactive form
automation).

## Source

Split out of `jupiter-browser-via-mcp` § May 2026 (2026-09-01, agent-bus:9853 G5).
Original findings from the [Prop 19 legal corpus ingest session](cursor-2026-05-03-1730)
(thread 879). Content unedited beyond the split and re-heading numbered findings
as top-level sections.
