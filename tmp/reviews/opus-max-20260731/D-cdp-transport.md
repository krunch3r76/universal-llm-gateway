# Workstream D — CDP transport

## Question

Pinned as asked, then sharpened (did not replace the brief's question):

1. **Primary (brief §3-D):** distinguish **"compose toggle broken"** from **"session unauthenticated or awaiting / stuck on Manual approval."**
2. **Standing:** is Playwright-driving `claude.ai` the right transport for CDP seats, or is the failure rate a property of the approach?

Changed: treated (1) as the binding first question; (2) is answered only after the failure classes are separated. Also pinned two cheap second-order defects: admission-as-success reporting, and the mangled `execution_id`.

## What you found

### Verdict: b7ea437d is approval-stuck, not toggle-broken

Event service `cdp.generate.stalled` at **2026-07-31T20:08:28.905Z** for
`execution_id=b7ea437d-9e08-4977-965a-e9fe1fcd7362`:

| Field | Observed value |
|---|---|
| URL | `https://claude.ai/new` |
| Mode before | `chat` (title `New chat - Claude`, approval null) |
| Mode after | `cowork` (title `New task - Claude`) |
| Attest | `ok=true`, `step=attested_cowork` |
| Approval after | `{"aria": "Manually approve"}` |
| Hint (old code) | `new_compose_toggle_failed` ← **mislabel** |

The Chat→Cowork toggle **succeeded**. Failure is in `set_approval_mode("auto")` leaving the chip on Manual. Same Manual fingerprint on the 10:13 fire (`6590-mission-close.md`). The 10:44 fire is a **third** class: URL `claude.ai/logout` / `chip_missing` = **unauthenticated**.

### Why the fleet conflated them

`libs/claude_bundles/chat_session_hygiene.py` `_compose_setup_error` (pre-fix) always set `hint=new_compose_toggle_failed` on bare `/new`, and preferred `result["mode"]` (the **successful** block) over `result["approval"]` when both existed. So b7ea437d's error body looked like a toggle failure while embedding a successful Cowork attest.

### Failure-class inventory (2026-07-31)

| Class | Fingerprint | Examples |
|---|---|---|
| **toggle** | mode not cowork / `chip_missing` while on `/new` and authenticated | true toggle bugs only |
| **approval** | mode attested cowork, approval still `Manually approve` | b7ea437d 20:08Z; 10:13 fire |
| **unauthenticated** | URL `/login` or `/logout` | 10:44 fire after disk reclaim |
| **reconcile abandon** | `stall_stage=reconcile_abandoned`, open leg > 4200s | e5bd9575 10:38Z |
| **admission misread** | `status=running, terminal=false` relayed as live window | agent-bus:6622 / next-arc-agenda |

Intermittent success after ~12:17Z means the approach is not categorically dead — incapacity claims from one or two failures were false (brief §4).

### Playwright-on-claude.ai — transport verdict

**Keep it for the operator-proxy / Cowork product seat; do not treat today's failure rate as proof the approach is wrong.**

- Anthropic Stargate API is prohibited for this seat class; the product surface (Cowork, skills chips, Authorize-triggers, Outputs) has no equivalent API.
- Observed failures today are mostly **session/auth**, **approval-mode automation**, **reconcile timeout**, and **claim-vs-observation** — not "Playwright cannot click Cowork."
- Residual fragility is real (DOM churn, cookie seed, Manual→Auto flake). Mitigations are classification, auth preflight, bounded approval retry, and honest admission envelopes — not abandoning CDP.

### Admission-as-success

Stargate CDP generate already returns `handoff_status=awaiting_first_reply` and `terminal=false` (`cdp_generate.py` ~362–392; `handoff_response.py` `_INITIAL_HANDOFF_STATUS`). The defect was **relayability**: seats treated `status=running` as arrival. Fixed on the satellite submit path in-territory (`SubmitProjectAskResponse`). Stargate `team_dispatch` path is out of territory — still needs the same fields or an explicit `phase=admitted` if not already consumed.

### Mangled execution_id — not a code truncation site

| Source | ID |
|---|---|
| Event service / stall payload | `b7ea437d-9e08-4977-965a-e9fe1fcd7362` (correct) |
| Bus subject | `cdp FAILED — b7ea437d` (`[:8]` only — intentional) |
| `format_cdp_result_body` | full correct id |
| Assertion a:27433 claim prose | `b7ea437d-9e08-497a-e9fe1fcd7362` (malformed) |

The mangled form appears in **operator-authored claim text** (a:27433 item 6), then echoed into `next-arc-agenda.md` / this brief as "the relay mangled it." No truncation function in `services/cdp-ask/**`, `libs/cdp_ask/**`, or `libs/claude_bundles/**` produces that string. Dropping chars `7-965` from the correct UUID reproduces it — consistent with LLM UUID garbling, not `[:N]` truncation. **Closeout relay attribution is not evidenced; residual is claim-prose hygiene.**

## What you changed

| SHA | Change |
|---|---|
| `5275247ea9bbbcb94985501a6ccdd525f7677b8e` | `classify_compose_setup_failure` + auth preflight + approval retry; tests `8 passed` on classify+cowork suite |
| `0acc3e308bd8b0a35f8d335fb2ab4718355cfe04` | Submit response: `terminal=false`, `phase=admitted`, `handoff_status=awaiting_first_reply`; MCP docstring honesty; `3 passed` on submit+client |

Key loci:

- `libs/claude_bundles/chat_session_hygiene.py` — `compose_auth_failure_hint`, `classify_compose_setup_failure`, rewritten `_compose_setup_error`, auth preflight in `goto_fresh_compose`
- `libs/claude_bundles/chat_cowork_mode.py` — one bounded Manual→Auto retry in `ensure_cowork_auto`
- `libs/cdp_ask/models.py` / `app.py` — admission fields on submit
- `services/mcp-server/tools/project_ask.py` — submit docs: admission ≠ arrival

## What you did NOT change and why

- **Did not replace Playwright transport** — failure classes above do not justify abandoning the product seat.
- **Did not touch Stargate `cdp_generate.py` / `team_dispatch` admission envelope** — outside file territory; satellite path fixed; Stargate already has `handoff_status` / `terminal=false` but may still need consumer-facing `phase=admitted`.
- **Did not "fix" UUID truncation in code** — no truncation site found; mangled id is claim prose (a:27433).
- **Did not abort/retry live CDP sessions** — observe-only; operator-gated.
- **Did not restart services** — constraint; see PROPAGATION REQUIRED.
- **Did not edit** `services/git_integration_worker/**`, `frontier.py`, `config/mcp/**`, cortex_store, charter-runner, `.gitignore`.

Note: commit `5275247e` also carried already-staged `tmp/reviews/opus-max-20260731/{B,C}-*.md` from parallel workers' index (shared-checkout hazard). Content was theirs; not rewritten.

## PROPAGATION REQUIRED

| Service | Why | SHA |
|---|---|---|
| **cdp-ask** (Jupiter satellite) | Loads `libs/claude_bundles` + `libs/cdp_ask` — classification + admission fields | `0acc3e30` (includes `5275247e`) |
| **mcp-server** (hub) | `project_ask.py` docstring / relay surface | `0acc3e30` |
| **universal-stargate** (optional, out-of-territory) | If CDP generate worker imports `claude_bundles` for compose setup on hub paths; also for mirroring `phase=admitted` on `team_dispatch` | land separately; not in these SHAs |

Landed-not-live until restart. Do not `force=true` drain.

## Open questions and residuals

| Residual | What would settle it |
|---|---|
| Why Manual→Auto click flakes (product refuse vs Playwright miss vs Authorize-triggers overlay) | Dogfood one `/new` launch after propagation; if `failure_class=approval` + `stuck_manual`, screenshot approval menu; if Kaywan Authorize dialog present, that is human gate not toggle |
| Is CDP profile authenticated right now? | Navigate ask profile; URL must not be `/login`/`/logout`; cookie seed from :9222 |
| e5bd9575 / reconcile_abandoned (70 min) | Poll satellite + inflight ledger for that sat id; separate from compose setup |
| Stargate `team_dispatch` still relayable as "live"? | Read live generate 202 body post-restart; add `phase=admitted` if consumers ignore `handoff_status` |
| Operator-reachable CDP window URL (a:27433 #6) | Architecture ask — `cdp-session-tracking-arch`; not fixed here |
| a:27433 claim still cites mangled UUID | Correct assertion claim text against event-service id (outside this seat's MCP-down constraint) |
