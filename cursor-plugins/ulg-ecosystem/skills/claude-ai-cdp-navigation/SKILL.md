---
name: claude-ai-cdp-navigation
description: "Jupiter CDP web-anthropic: Cowork default on /new; Chat operator opt-in (25051/25052 dogfood 2026-07-19); project-ask bus-nudge; multitask; skill inject ≠ GitHub; parallel Chrome; OptGuide disk."
trigger_match_terms:
  [
    "claude-ai-cdp-navigation",
    "claude_ai_cdp_navigation",
    "claude.ai",
    "cdp",
    "cowork",
    "project-ask",
    "project-chrome",
    "fable",
    "opus",
    "sealed-ask",
    "jupiter-chrome",
    "skills.enabled",
    "cowork-skill-delivery",
    "web-anthropic",
    "jupiter-cdp",
    "agent-bus",
    "push",
    "push_reminder",
    "bus-nudge",
    "handoff",
  ]
related_skills:
  [
    "jupiter-browser-via-mcp",
    "claude-ai-mcp-connect",
    "claude-ai-bundle-sync",
  ]
---

# Claude.ai CDP navigation (Jupiter)

`automate(claude.ai) ⇒ Jupiter CDP ∧ logged-in profile ∧ ¬fight peer Chrome`.

**Scope:** ecosystem census — hub + satellite. Code: `libs/claude_bundles/` + Jupiter `cdp_ask`. **Not** public APIs. Admission settled (L3 `reference-annex.md` § Settled gate).

## When to load

- Sealed ask / ralph / Fable consult via Cowork Project or `/new`
- **web-anthropic `push_reminder`** — CDP autonomous wake is default; human push reminder only when CDP unavailable/broken (`agent-bus-push-reminder_ulg.mdc`; 24628 / operator bind 2026-08-06)
- Multi-stop bus ping-pong / empty out-dir / shell abort — L3 annexes (24831–24976)
- Project chrome; parallel Chrome lane; harvest / model-picker failures
- **Chat vs Cowork on `/new`** — § Chat vs Cowork (25051/25052)

Cross-ref: `jupiter-browser-via-mcp` · `claude-ai-mcp-connect` · `claude-ai-bundle-sync`. Cowork operator-proxy: load **`cdp-operator-proxy`** — this skill stays transport/CDP.

**RAG (BINDING):** executing agent calls `rag(op=search)` when MCP-enabled; staged sidecar = priming only; **¬** lead merge (`decision:cdp-rag-via-mcp-not-lead-merge`). **Default product:** `team_dispatch(model=cdp/…)` → poll `poll_hint`. **`project_ask` MCP** = escape / satellite-direct surface (`op=submit` → poll; `op=followup` warm paste on attached lane).

## Dual-completion poll ladder (BINDING — a:25662)

```
running → turn_idle → content_proof → archiving → terminal | failed
```

| Rung | Consumer meaning |
|---|---|
| `running` ∧ `stall_stage=null` | **In flight** — keep polling; wall-clock alone ≠ stall |
| `turn_idle` | CDP turn idle — **≠ advance-eligible** alone (24864) |
| `content_proof` | Durable sidecar + idle — advance only after **consumer fs-read + sha re-verify**; must **not** trigger `delete_after` |
| `archive_uri` / `terminal` | Harvest terminal |
| `failed` + `stall_stage` | Stall lane — **≠ running** |

**Long-running ≠ stalled:** Opus/Cowork may sit `running`+`stall_stage=null` many minutes. Forbidden: duration → `cdp_unavailable`, abort-and-skip, or Stage-B without harvest proof.

**Operator-proxy CSE retain (BINDING):** for `purpose=operator-proxy|mission`, the generate poller must **not** Stop-click / kill the CSE on `max_wall_s` or `no_progress_s`. Idle between DIRECTIVE legs is expected. Clean CSE break is allowed only for **continuity handoff** (after a new CSE launch is confirmed) or rare human escalation — see `cdp-operator-proxy` § CSE lifetime. `wall_clock_exceeded` on a mission is poller-detach / false FAILED if it still appears — reattach; ¬ treat as arc dead.

**Operator self-stop is a different plane (BINDING — 2026-08-01):** poller retain does **not** authorize the operator seat to end its Cowork turn. `end(stream) ⇔ continuity_handoff ∨ TYPE:MISSION_CLOSEOUT` — full discriminator + exception notify (`cse-stream-stop`) live in `cdp-operator-proxy` inv 30. IDE observing mid-mission idle with open residuals and no mission-close TYPE ⇒ load that skill and fire the awareness ping if Opus already went quiet.

**Post-idle cadence (a:26348):** once `turn_idle` **or** `stop=true`, re-poll **≤5–10s** until `archive_uri` | verified `content_proof` | `failed`+`stall_stage`. Chat may skip `content_proof_uri` — do not wait for a rung that never comes. Forbidden: multi-minute sleeps between polls.

**Paste into a live CSE is ALWAYS allowed (BINDING — operator 2026-08-01):** Cowork is **always in multitask mode**. A follow-up paste into a streaming/working CSE is accepted and picked up — it does **not** interrupt, supersede, or corrupt the running leg. Forbidden: gating a paste on `stop=false` / idle, wait-for-idle poll loops before `send_followup_paste_half`, or abandoning a paste because the session "looked busy." Paste immediately; mark the note non-blocking in its own body if the running leg has priority. (Bus post + CSE paste are separate channels — if the bus turn already landed and was answered, skip the paste rather than duplicating.)

**Held-page (25671):** held Playwright page only; **either-proof advance:** verified `content_proof` **OR** `archive_uri`; never `turn_idle` alone. `delete_after`: archive-proof or attested abort — L3 `operations-annex.md`.

**Artifact / Outputs harvest (BINDING cue):** Cowork **Outputs** + artifact/canvas are the deliverable surface; **Google Drive** in the card toolbar is open-with chrome (often shown with **Download** in the same menu). Structured CDP asks must admit with Outputs-first knobs (`expected_size=large` / `download_output=true`) — chat scrape alone collapses the card. Substrate now attempts in-chat Document/MD card body extract after Outputs/cortex-uri (`artifact-card` provenance) and fail-closed `artifact_card_without_body` when unresolved — **re-request on a live CSE is the exception path** for legacy archives or residual DOM drift, not the close path. Detail + prompt duty: L3 `operations-annex.md` § Cowork Outputs-first / File-card chrome.

## FOL pipeline

```
ensure_chrome(port, profile) ⇒ attach_cdp
⇒ pick_chat_page(prefer /chat/ ∨ /new; ¬ Project shell for harvest)
⇒ [project_uuid ⇒ goto(project_url)] ∨ goto(/new)
⇒ [/new ⇒ ensure_cowork_auto]   # Cowork + Auto default (25051/25052)
⇒ [/new ∧ operator Chat ⇒ ensure_chat_compose]
⇒ select_model(<live UI>)  # picker SOT — ¬ harness allowlist
⇒ click(Start task ∨ Send message)  # ¬Enter
⇒ wait(complete(turn))
⇒ harvest ∧ strip_thinking ∧ attest(model)
⇒ persist(raw_harvest → cortex_sidecar)
⇒ [ask ⇒ delete_chat ∧ goto(return_url)]  # only if validated(archive)
⇒ [converse ∧ --close ⇒ delete]  # never auto-delete on converse
```

## Chat vs Cowork (compose on `/new`)

**Operator bind (25051/25052):** **Cowork + Auto default** on bare `/new`. Chat = operator opt-in (`--chat` / `chat_compose=true`) only.

```
∀ new project-ask on bare /new:
  ensure_cowork_auto MUST attest mode=cowork ∨ approval present
  select_*_no_attest ⇒ reopen friction — NOT "retry Chat"
  submit(/new) ⇐ Start task ∨ Send message; submit(warm) ⇐ live_discover  # ¬ Enter
```

| Dispatch | Default | Opt-in |
|---|---|---|
| Automated `/new` | Cowork + Auto | — |
| Short bus-nudge Chat | Chat | `--chat` / `chat_compose=true` |
| Bound Project UUID | Cowork Project shell | automatic |

`/new` converse: `--converse --no-uuid` (not `--no-uuid` alone — 24611). Mechanics: `libs/claude_bundles/chat_cowork_mode.py`.

### Cowork is multitask — mid-run context does NOT interrupt (operator bind 2026-08-01)

```
∀ live Cowork CSE mid-run:
  send(context) ⇏ interrupt(agent)          # queues; agent picks up
  agent MAY: read → judge relevance → act ∨ defer
  ⇒ ¬ withhold(correction) on "don't interrupt" grounds
```

A message delivered into a running Cowork session is **not** a preemption. The
seat reads it, decides whether it bears on the current task, and either folds it
in or defers it. Cowork is a multitask surface by design.

**Consequence for seats:** "I'll wait for harvest so I don't derail the run" is
**not** a valid reason to hold a correction, a falsifier, or newly-bound operator
context. That reasoning treats a live CSE as an archive, which § Warm follow-up
duty already forbids. Deliver it; let the seat triage.

Still true, and not in tension: **rate and form** govern (observation-shaped, not
verdict-shaped), and a *fresh* dispatch is a different act with real cost. This
clause removes the interruption objection only.

## web-anthropic-cdp dispatch constraints (BINDING — 24906)

```
∀ dispatch(web-anthropic-cdp ∨ project-ask ∨ Cowork bus-nudge):
  prefer_package(corpus → cortex://)   # fewer tool calls; faster, more targeted
  workspaces:// readable on web        # ¬ claim unreachable
  ¬ rely(GitHub connector) for codebase browse
  ¬claude_slug(skill) ∧ required(skill) ⇒ inline_excerpt(sealed_prompt)
  rag_activation ≡ agent_MCP_rag  # ¬ lead merge
```

| Factor | Consequence |
|---|---|
| Corpus packaging | **Prefer** stage hot paths to `cortex://` (fewer tool calls, faster response). `workspaces://` is readable on web — do **not** claim otherwise. When exploration is encouraged, the packet **MAY** explicitly name `workspaces://` as an option |
| GitHub connector down | ¬ "read repo via GitHub" |
| Skill not Claude slug | Inline excerpts; cortex stage backup only |
| RAG priors | Executing agent calls `rag` via MCP when enabled |
| Override | Operator documents in packet |

### Sealed / unattended — no clarifying questions (BINDING — a:26156)

Sealed CDP / tick-charter R-admit have **no human in loop**. Cowork questions = false-complete class.

```
∀ sealed ∨ unattended CDP:
  packet MUST: answer with best judgment; state assumptions;
  ¬ clarifying questions; ¬ wait for human reply
```

**Override:** operator-proxy episodes requiring operator/cursor ping — omit/negate clause; see `cdp-operator-proxy`.

### Skill delivery — fleet rule (BINDING)

| Skill class | Delivery |
|---|---|
| **Claude slug** | Manifest `/<slug>` lines → **`+` → Skills → pick** (attach); hybrid `Use the …` escape |
| **Not Claude slug** | **Inline** excerpt into sealed prompt; ¬ slash; cortex stage alone ≠ delivery |

**CDP flow:** stage → `attach_session_skills` (+ → Skills) → attest membership → paste body. Fail closed before submit. Packet-class sets + anti-patterns: L3 `reference-annex.md` § Skill delivery detail.

## Completion predicate (MUST)

```
complete(turn) ⇐ assistant_body ∧ ¬streaming ∧ ¬Stop ∧ stable_length
  ∧ ¬error_banner ∧ turn_count_incremented ∧ ¬tool_pause_state
```

`¬ complete(h) ⇒ ¬delete ∧ friction`. Stop detection only in generation/composer roots — sidebar Stop excluded (24873). `error_banner`: banner/toast only, exclude composer (25486); `Overloaded` may linger after completion — structural completion wins (25684). Cowork CSE fallback + detail: L3 `operations-annex.md`.

### Reading the harvest — chrome ≠ delivery (BINDING — operator 2026-08-11)

The scraped CSE body carries claude.ai **UI chrome** alongside assistant prose: artifact
cards, "Used … integration" banners, and **suggested connector** affordances. The fleet does
**not** use those connectors — Drive/Docs/etc. are product suggestions the scrape renders, not
destinations anything wrote to.

```
connector_name ∈ harvest ⇏ artifact_written(connector)
artifact_card ∈ harvest ⇏ body ∈ harvest
```

| Harvest shows | Means | ¬ Means |
|---|---|---|
| `<title>` / `Document · MD · Google Drive` under a card | Cowork **artifact** exists in-session; Drive is a *suggested* save target | The seat wrote to Drive; the file is fetchable there |
| `Used toys integration` / `updated tasks` | Product-surface activity banner | A durable fleet write happened |
| Card title present, body absent | **Delivery gap** — harvest did not pull the artifact body | The deliverable is lost, or went to a connector |

**Delivery surfaces that count:** bus turn body · `cortex://` write (quote `written_sha256`).
A Cowork document artifact is **neither** until harvested into one.

`card_without_body ⇒ re-request(blocks inline in reply body ∧ cortex:// write)` — a thin
follow-up naming the missing blocks, ¬ a rework of the judgment, and ¬ a "do not use
<connector>" instruction premised on a misread. Packet `<output_format>` SHOULD say *reply
body + `cortex://`* explicitly so the seat does not answer into an artifact card.

## Parallel Chrome (BINDING)

**DEFAULT:** `register_lane` / `project-ask --register`. Soft=**2**, hard=**3** concurrent — use `free_slots` / `at_hard_limit` from `effective_count = max(running_count, live_cse_count)`, not `busy` or recorded-only counts alone (a:25814). `--no-register --cdp-url :9222` = attended primary only. Registry + OptGuide + orphan observability: L3 `reference-annex.md`.

## Entry points (bus-nudge minimum)

| Job | Path |
|---|---|
| **Product (DEFAULT)** — consult / binder / R-admit / judgment_gap / Fable outside-check | `team_dispatch(op=generate, model=cdp/opus-5\|cdp/fable, contract=light-bounded, prompt\|sidecar_ref=…, dispatch_thread_id=…)` → poll `poll_hint` (`agent_bus.wait`). Compose `lean-context-dispatch-first` · `consult-routing`. |
| **Escape** — satellite-direct / MCP-only / IF6 | `project_ask(op=submit, prompt_uri=cortex://…, converse=true, no_project_uuid=true, model=opus-5\|fable-5)` → poll. Use when `team_dispatch` CDP path is unavailable or caller must bypass Stargate admit. |
| **Warm follow-up (attached lane)** | `project_ask(op=followup, chat_url=… \| registration_id=… \| execution_id=… \| identity omitted ⇒ resolve-or-refuse, cdp_url=… explicit override, prompt_text=… \| prompt_uri=…, purpose=operator-proxy, timeout_s=60)` — wake/correction/advisory into retained operator-proxy CSE; `project_ask(op=resolve_attended)` for read-only triple. CLI `cowork_chat_followup.py` = escape (defaults to resolver when flags omitted). |
| **Operator-proxy mission** | Prefer `team_dispatch(model=cdp/opus-5, purpose=operator-proxy\|mission, contract=light-bounded, …)` — runner auto-ensures `/cdp-operator-proxy` + `/reasoning-posture` chips + seat-map briefing (`operator_proxy_mission.py`). `project_ask(purpose=…)` remains escape. Prompt body still carries mission ACs. SOT: `cdp-operator-proxy` inv 20 · `cortex://notes/system/specs/cursor-auto-tick-work-posting.md` |
| Operator Chat on `/new` | `chat_compose=true` / `--chat` |
| Register / list | `list-lanes` / `deregister-lane` |

**Anti-pattern:** defaulting binder/consult CDP to bare `project_ask` when `team_dispatch(model=cdp/…)` is available (`lean-context`: project_ask = escape only).

### Warm follow-up duty (BINDING — 2026-07-31)

A retained operator-proxy CSE is a **live correspondent**, not an archive. Reach it in chat.

| Situation | Move | Receipt |
|---|---|---|
| Wake / correction / ladder-fix / advisory to a **retained** CSE on an **attached** lane | `project_ask(op=followup, …)` — identity omitted ⇒ attended resolve-or-refuse on satellite (`target_binding=resolver`); explicit `cdp_url`+`chat_url` ⇒ `target_binding=explicit`. Paste-verified (`send_verified` / `receipt`), no reply harvest | `dom_paste` default; `dom_committed` when reload retains marker in committed user-turn nodes |
| New turn with no retained CSE — or its context is stale / Customize skills refreshed | `team_dispatch(model=cdp/…)` (default) · `project_ask(op=submit, …)` (escape) — a **fresh window**, ¬ warm paste | n/a |
| Audit trail for either | bus turn **accompanies** — ¬ substitutes | n/a |

`in_chat_delivery ≻ bus_NOTE` · identity ladder `chat_url ≻ registration_id ≻ execution_id` · **v1 = attached lane only** (no post-deregister reattach). **Launch-path paste** (reattach mints satellite lane) proves **satellite-scope** DOM only — relaying `ok=true` / `send_verified=true` as human/CSE-seat delivery when `lane_created=true` is the **a:27855** failure class.

#### Followup failure triage (2026-08-01 — do not misread the error)

| Error | What it actually means | Next move | Receipt note |
|---|---|---|---|
| `lane_not_attached` after passing **only** `execution_id` | Often **wrong id space**. A `cdp/*` `team_dispatch` returns a **Stargate** id; the satellite mints its own (see harvest archive `execution_id:`). The resolver maps exe→registration and **bails before scanning any lane** when that lookup misses — so this error does **not** prove the CSE is gone | Retry with `chat_url` (highest precedence — skips the mapping and scans all lanes), or with the **satellite** id from the archive | n/a |
| `cse_not_found_on_lane` | Lanes were scanned; the page is **not open** on any attached lane. The URL may still be perfectly valid | Honest dead end for v1 — see below | n/a |
| Both, with `list_active()` empty | No attached Chrome lane at all; the lane was torn down after harvest | Fresh `team_dispatch(model=cdp/…)` | n/a |
| `human_visible_receipt_unavailable` | Caller requested `min_receipt=human_visible` — unsatisfiable in v1; zero side effects | Do not relay as delivery; use attended session or bus | `receipt=None` |
| `send_unverified` with `receipt=dom_paste` | Paste proven in automation DOM but caller gate was `dom_committed` | Retry or accept satellite-scope paste proof | partial |

**Why a valid URL is not enough:** warm followup drives a browser page that is
**already open** on an attached lane. `followup_resolve` "never registers lanes,
opens profiles, or navigates to CSE URLs" — by design. So reattach is blocked by a
missing *navigate-an-attached-lane-to-a-known-CSE-URL* step, not by the chat
expiring. `¬` report this to an operator as "the session is gone."

**Anti-patterns:** bus NOTE + operator push reminder standing in as the *delivery* of a wake the seat could have read in chat; reaching SSH-first for `cowork_chat_followup.py` from an IDE seat that holds the MCP (CLI is the escape, for hub checkout / no attached lane); `op=submit` onto `/new` for a turn that belongs on a retained CSE; describing shipped followup as an "MCP gap" or "feature candidate"; relaying `ok=true` / `send_verified=true` from a **launch-path** (`lane_created=true`) paste as human/CSE-seat delivery (a:27855).

Full FOL + escape recipe: L3 `reference-annex.md` § Warm follow-up.

Full matrix + `delete_after`: L3 `reference-annex.md` § Entry points.

## Poll guardrail — project_ask ports

**Preferred CDP path:** `team_dispatch(model=cdp/opus-5|cdp/fable|…)` → poll
`poll_hint` with `agent_bus(tool="wait", …)` (`consult-routing` · this skill).
Operator-proxy missions:
`team_dispatch(…, purpose=operator-proxy|mission)`. Do **not** default new
consults to bare `project_ask`.

When an in-flight **escape** `project_ask` execution must complete (satellite-direct /
IF6 / legacy purpose= escape):

**POLL GUARDRAIL — project-ask executions**

- NEVER curl, fetch, or HTTP GET/POST to localhost/127.0.0.1 — especially **:8765** — for `/v1/project-ask/*`.
- Port **8765** is **web-fetcher**, NOT project-ask. The cdp-ask satellite listens on **:8770** (`PROJECT_ASK_URL`).
- Poll ONLY via MCP: `project_ask(op="poll", execution_id="<id>")` — repeat until `archive_uri` is set.
- Completion proof: poll response `archive_uri` (cortex:// harvest). Verify via `mcp.project_ask.poll` events.

```
# Escape only — prefer team_dispatch(model=cdp/…[, purpose=operator-proxy]) for new consults/missions
project_ask(op="submit", prompt_uri="cortex://…", converse=true, no_project_uuid=true, model="opus-5")
# → execution_id

project_ask(op="poll", execution_id="<id>")
# repeat until status=completed and archive_uri is set
```

CLI/SSH dogfood (`claude-ai-sync-jupiter project-ask`) is hub-checkout fallback only — not the agent product path.

## Related skills

`jupiter-browser-via-mcp` · `claude-ai-mcp-connect` · `claude-ai-bundle-sync`

Companion stub: `.cursor/rules/claude-ai-cdp-navigation_ulg.mdc`.
