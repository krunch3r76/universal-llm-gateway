---
name: web-automation-discipline
description: "Before Playwright/CDP form fills, uploads, place/checkout clicks, or multi-step SPA interactions on authenticated sites — probe-first selectors, file-chooser hooks, seat routing, Place→Orders verify, production anti-patterns."
trigger_match_terms: ["CDP automation", "browser automation", "chosen.js", "expect_file_chooser", "file upload", "form fill", "governmentjobs", "neogov", "playwright", "web-automation-discipline", "Place Order", "PLACE null", "orders verify", "DoubleDash"]
generator_version: "1.0.0"
---

# Web Automation Discipline

**Invariant:** `probe → bind selectors → one bounded action → verify on page` — never guess selectors across a JS SPA; never remove persisted UI state before the replace path is proven.

Load `jupiter-browser-bringup` for Jupiter Chrome bring-up and `jupiter-browser-via-mcp` for `browse`/`save_to` and download transport. This skill owns **interactive form automation** (uploads, dropdowns, multi-step apply flows).

## Seat routing

| Need | Seat | Why |
|---|---|---|
| Signed-in Jupiter profile (`claude-ai-chrome-profile`, NeoGov, Uber, Scribd) | **SSH + Playwright `connect_over_cdp("http://127.0.0.1:9222")` on Jupiter** | Cookies live on Jupiter; cursor-ide-browser and CDP Cowork are different sessions |
| Quick read / short verify on public page | `cursor-ide-browser` | Fine for smoke; no file-input on IDE proxy |
| Stuck after one probe + one fix attempt | **CDP Opus consult** (`team_dispatch(model=cdp/opus-5)`; `project_ask` escape only) | Cowork cannot reach Jupiter CDP — ask for selector strategy, execute on Jupiter yourself |
| Long-form extraction | `dispatch(tool="browse")` | See jupiter-browser — not for file uploads |

`CDP Cowork ¬ substitute for Jupiter Playwright` when the task is mutating an authenticated form.

## Pre-flight (always)

1. **Observed session state — never assumed** — probe current URL, DOM, or CDP `/json/list` **immediately before** harvest or mutation. Prior-arc or prior-dispatch state is stale; do not instruct credential entry from remembered pairing. (agent-bus:6008 CLOSEOUT-6; agent-bus:6032 AC1)
2. **Reattach, don't respawn** — `connect_over_cdp` to the existing profile/context/tab. Launching a new Playwright persistent context duplicates cookies and spawns parallel processes unrelated to the session you meant. (agent-bus:6008 CLOSEOUT-7; agent-bus:6032)
3. **Reuse the live tab** — `browser.contexts[0].pages` with matching host; `bring_to_front()`. ¬ open a new tab per step (CSRF/Knockout view-model lives in the SPA instance).
4. **Auth probe** — page text or URL must contain the expected signed-in marker before any mutation.
5. **`--probe` before `--act`** — dump visible controls in the target section: tag, id, `aria-label`, `data-bind`, bounding rect. **Read the probe before acting** — if the probe shows both a passwordless path (QR / device pairing / magic link) and a credentialed path, default passwordless; credential fields = stop-and-report, never fill unless operator explicitly bound credentials. (agent-bus:6008 DIRECTIVE-7, DISPOSITION t41)
6. **Navigation** — `wait_until="domcontentloaded"` + explicit `wait_for_selector` / short sleep. ¬ `networkidle` on analytics-heavy sites (NeoGov, governmentjobs.com).
7. **Scroll** — off-viewport Chosen.js / upload widgets report `not visible` until `scroll_into_view_if_needed` on the section container.

## CDP transport & liveness

| Signal | What it means |
|---|---|
| "No CDP activity visible" | **Not evidence of death** — check actual transport before concluding |
| `--remote-debugging-pipe` | Playwright internal pipe; **no listening port** on 922x — `ss -tlnp` shows nothing |
| Headed browser on Xvfb `:N` | Invisible to physical display and to localhost CDP watchers on a different surface |
| Two browsers, two DISPLAY values | Both can be live simultaneously — identify which profile/host/display you attached to |

Probe order: listening port vs pipe → which `DISPLAY` → pid/heartbeat/script status → DOM URL. (agent-bus:6008 CLOSEOUT-4)

## Virtual-scroll / lazy-rendered lists

Before extract: scroll to top repeatedly until message/item count stabilizes (3 consecutive stable reads), then scroll back to bottom if needed.

Incremental re-extract: anchor on a **matched prior content string**, not a raw DOM index — indices shift when lazy rendering loads history. Attachment or non-text rows may not match the primary text selector; a count gap of one is not always a missing message. (agent-bus:6032 deltas_to_spec)

**Cursor dispatch sandbox (2026-07-27):** dispatch `HOME` points at an empty sandbox — set **`HOME=$HOME`** and **`PLAYWRIGHT_BROWSERS_PATH=$HOME/.cache/ms-playwright`** on Playwright launch or Chromium cannot be found.

## File uploads

**Tier 1 (default):** hook the chooser, not the input.

```python
async with page.expect_file_chooser(timeout=15_000) as fc_info:
    await trigger.click()
await (await fc_info.value).set_files(str(path))
```

`input[type=file]` may be absent until click, hidden (`display:none`), or inside a plupload shim — chooser hook still fires.

**Tier 2:** `locator('input[type=file]').set_input_files()` — works when input exists in DOM (visibility not required).

**Tier 3:** CDP `DOM.setFileInputFiles` with `pierce: true` — last resort.

**Trigger selection:** scope to the attachment/box container; exclude known false positives (`#top-resume-upload` = affiliate promo on NeoGov).

## Replace vs remove (attachments)

| Pattern | UI behavior | Safe automation |
|---|---|---|
| **Required** attachment type | Empty slot keeps heading + Upload affordance | Remove → Upload in same box |
| **Supplemental** attachment (NeoGov) | Removing file **deletes the whole box**; count drops | **Add supplemental attachment** → choose type → upload. ¬ remove-then-upload |
| Any | AJAX delete is immediate | Prove upload path on a throwaway slot **before** removing production file |

Recovery ladder (NeoGov-style): hard reload attachments → Add supplemental → type dropdown → upload → verify on **Review** tab (not sidebar count alone).

## Dropdowns (Chosen.js and kin)

NeoGov uses Chosen.js: visible control is `a.chzn-single` inside `.chzn-container-active`, not a native `<select>`.

```python
await page.locator(".chzn-container-active a.chzn-single").first.click()
await page.locator(".chzn-results li", has_text="Cover Letter").first.click()
```

¬ `get_by_role("link", name="Choose attachment type")` when the widget is a `javascript:void(0)` anchor with nested span. ¬ `.last` on `chzn-single` — hidden duplicate containers exist (`w:0 h:0`).

## Selector rules

| Bad | Good |
|---|---|
| `text=Cover Letter` (global) | `div.box:has(h3:text-is("Cover Letter"))` — footer privacy `<li>` also matches bare text |
| `button:has-text("Upload")` | Scope to box; exclude `#top-resume-upload` and `aria-disabled` |
| `locator("text=…").first` without visibility check | Filter `is_visible()` or use probe dump |
| Sidebar "Attachments N items" | Review tab filename line — source of truth for upload |

Prefer: `aria-label` on remove links (`Remove cover letter …`), `h3:text-is(…)`, `data-bind` hints from probe.

## Verify

Completion = **observed on the target page after reload**, not absence of Playwright error and not a tool-print timeout.

- Upload flows: navigate to **Review** (or equivalent summary), read filename line.
- Capture `inner_text` snippet or screenshot path in closeout.
- Required-attachment banners may lag — if file appears on Review but banner persists, note as `partial` and flag for operator glance.

### Place / checkout mutation (hop law)

After any Place / Submit Order click (or equivalent money-moving confirm):

1. Outcome is ternary: `placed` | `not_placed` | `unknown` — never binary from the tool print alone.
2. `PLACE null` / Playwright async timeout / tool-print silence ⇒ `unknown` until the site Orders surface is read.
3. Before any place or not-placed claim: observe terminal confirmation — the order/confirmation ID on the host's Orders surface. DoorDash: `/orders` (or live order card) **and** the order UUID (e.g. DoubleDash `08fc8248`). No UUID ⇒ stays `unknown` (never `not_placed`). `not_placed` requires an Orders read ≥15s after the click showing no order created after the click time, **and** the checkout surface still showing the cart unplaced or an error. Otherwise stay `unknown` and escalate to the operator. No retry.
4. While outcome is `unknown`: no next hunt, no second Place, no cancel-to-retry.
5. Operator `checkpoint` during shop ⇒ **STOP** all shop acts (no rehunt, no Place). Reading Orders to resolve an unknown place outcome is exempt from the halt and comes before the checkpoint records place state. If it can't be read, the checkpoint records `unknown`, never `not_placed`. Resume-protocol detail lives on `checkpoint-discipline`; this skill binds the mutation halt.

Class: mutation without terminal verify (related a:32030). Specimen a:38478 — Place clicked ~2:06 PT, tool returned PLACE null, seat reported not placed; live order UUID `08fc8248` had already placed.

## Anti-patterns (binding)

| Anti-pattern | Consequence |
|---|---|
| Remove attachment before upload path proven | Empty supplemental slot vanishes; count drops; recovery needs re-add flow |
| New CDP tab per action | Timeouts, logged-out appearance, duplicate Knockout roots |
| `networkidle` navigation | 90s timeouts; session looks dead |
| Hunt `input[type=file]` without chooser hook | False "no inputs" when inputs are lazy |
| Trust CDP Opus to execute on Jupiter | Cowork has no Jupiter CDP — consult only; implement on Jupiter |
| `cancel-link` / delete without operator ack on irreversible forms | Persisted AJAX — no undo |
| Assume session state from a prior arc without URL/DOM probe | Unnecessary credential prompts or wrong-surface work (6008 CLOSEOUT-6) |
| Click Sign In when probe shows a passwordless pairing control | Operator declined credentials; use passwordless path or stop (6008 D-7) |
| Infer browser dead from absent CDP port activity | `--remote-debugging-pipe` and Xvfb holds are invisible to port watchers (6008 CLOSEOUT-4) |
| Launch new persistent context when CDP attach works | Duplicate parallel browsers; wrong session (6008/6032) |
| Incremental extract by raw DOM index on virtual-scroll list | Index shift under lazy render; anchor on prior content string (6032) |
| Treat Place tool timeout / `PLACE null` as `not_placed` | False negative; live order already placed; seat hunts again (a:38478) |
| Claim placed / not-placed without Orders + order-UUID read after Place | Mutation without terminal verify (class a:32030) |
| Treat a too-early empty Orders read as `not_placed` | Double Place / double charge while the first submit is still in flight (a:38478 reverse) |
| Continue shop acts after operator `checkpoint` | Halt ignored; further Place/hunt risk (mutation stop here; protocol on `checkpoint-discipline`) |
| Record `not_placed` at checkpoint when Orders could not be read | Same false negative as PLACE-null (a:38478) |

## Escalation

After **one** probe-backed fix attempt still blocked:

1. Package: URL, auth marker, probe dump, last error, what must not be clicked.
2. `team_dispatch(model=cdp/opus-5)` CDP Opus — selector/strategy consult (archive to cortex). Escape: `project_ask` when team_dispatch CDP unavailable.
3. Implement fix on Jupiter; do not wait on Cowork to drive the browser.

## Reference implementation

Script pattern (Jupiter): `tmp/neogov_upload_cover_v5.py` in hub checkout — supplemental re-add flow validated 2026-07-27 (governmentjobs.com Santa Clara 26-R27-D).

Cross-refs: `jupiter-browser-via-mcp` (transport) · `jupiter-browser-bringup` (Chrome/web-fetcher/cdp-ask infra) · `claude-ai-cdp-navigation` (CDP consult fire) · archive `cortex://notes/system/threads/cdp-ask-archive-new-0edef9fc.md` (NeoGov diagnosis). Site-specific runbooks (Google Messages web, Operation PAGER pairing) live in cortex `notes/system/runbooks/` — not in this skill.

## Site maps (per-host)

Authenticated hosts that will be driven again get a **map** (selectors + hop list) plus **named scripts**. Do not rediscover SPA selectors in chat when the map exists.

| Kind | Map | Scripts |
|---|---|---|
| Banks / issuers | `runbook:issuer-portal-login` → `cortex://notes/runbooks/issuer-portals/{issuer}.md` | `scripts.local/issuer-portals/` |
| Other hosts (retail, …) | `runbook:site-maps` → `cortex://notes/runbooks/site-maps/{host}.md` | `scripts.local/site-maps/{host}/` |

Walgreens **shop/pickup** is `runbook:walgreens-pickup` (not ESS `scripts.local/walgreens/`). Call the hop script; probe only when the map is stale.
