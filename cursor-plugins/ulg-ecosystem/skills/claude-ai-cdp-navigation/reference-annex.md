# Claude.ai CDP navigation — reference annex (L3)

Load on demand for dispatch shaping, registry detail, model lanes, and settled-gate evidence. L2 must suffice for a flat-upload `project-ask` bus-nudge.

## Cowork multitask prompt shaping (SHOULD — 2026-07-17)

Cowork can **fan parallel agents** inside one task. Prompt authors must **ride that harness**.

```
independent_tracks(T1…Tn) ∧ |Ti|≥2 ∧ each(Ti) has cortex URIs + named deliverable
  ⇒ prompt MUST declare parallel fan → join synthesize
¬ dump one undifferentiated wall that forces serial tool walks
```

| Do | Don't |
|---|---|
| Name **2–5 parallel tracks** with goal + `cortex://` URIs + output shape | One megaprompt that reads all recon serially |
| Explicit join: "after agents finish, parent synthesizes ranked table / verdict" | Leave synthesis implicit |
| Skills / corpus: obey L2 § web-anthropic-cdp dispatch constraints | Cite `workspaces://` as readable |

**Template sketch:**
```
## Parallel tracks (Cowork multitask — fan then join)
Track A — <goal>: read <cortex://…>; deliver <shape>
Track B — …
Parent join — synthesize <L0 table | verdict | ranked bind>; ¬ re-open tracks.
```

Cross-ref: `expand-growth-loop_ws.mdc` · `agent-bus-multitask` (our seat's fan — different substrate).

## Chat vs Cowork — mechanics detail

| Control | UI | Code |
|---|---|---|
| Mode | Chat ↔ Cowork chips (title `New chat` ↔ `New task`) | `ensure_cowork_auto` / `ensure_chat_compose` |
| Approval | Manual → **Automatically approve** (Cowork only) | `set_approval_mode("auto")` |
| Submit (bare `/new`) | Cowork: **Start task** · Chat: **Send message** | `send_prompt` — `await_submit_visible`; ¬ Enter |
| Submit (warm `/cowork/cse_*`, `/chat/*`) | Composer-local **Send** | `discover_live_submit`; composer refocus + 300ms settle |

```
ensure_cowork_auto(page)  ⇐ select(Cowork) ∧ set(Automatically approve)
ensure_chat_compose(page) ⇐ select(Chat) ∧ attest(title New chat)
submit(/new) ⇐ Start task ∨ Send message  # mode_locked; ¬ Enter
submit(warm) ⇐ resolve_submit_strategy → live_discover → discover_live_submit
```

CLI: default Cowork; **`--chat`** operator opt-in only. MCP: `ensure_cowork_auto=true` default; **`chat_compose=true`** operator opt-in. Legacy `--cowork-auto` is no-op. Parallel CDP: `:9223 --profile-suffix ask` — ¬ attach peer `:9222`.

GitHub connector / extended tooling: Cowork mode exposes Project + connector tooling; Chat mode will not.

## Converse batching (SHOULD — friction 24831)

**Soft ceiling ≤3 turns/session** when batching is natural; exceed only with named reason.

```
∀ multi-stop agent-bus ping-pong ∨ meaning-walk:
  project-ask --converse ⇒ prefer pack ≤3 turns per session
  (repeatable --prompt-file t1.md --prompt-file t2.md --prompt-file t3.md)
```

| Do | Don't |
|---|---|
| Batch 2–3 consecutive meaning-walk stops in one `--converse` | Default 1 turn/session for multi-stop queue |
| Start **new** converse for craft mode or after operator gate | Pack craft + meaning stops into one chat |

**Exempt:** operator yes/no/mint gates; craft/authoring mode; stops needing graph verify-first.

Companion: `.cursor/rules/jupiter-converse-batching_ulg.mdc`.

## Path-sim R-admit (BINDING — friction 24967)

CLI SOT for path-sim phase-3 R / disposable code-review asks. Cascade policy: Use the `path-sim` skill § Lead R.

```
∀ path-sim R-admit ∨ disposable code-review project-ask:
  surface = /new
  ⇒ --register --purpose ask --converse --no-uuid --model opus-5
     --prompt-file <checkout-relative tmp/reviews/…>
  ¬ --uuid <endeavor|falsifier PROJECT constant>
```

| Rule | Detail |
|---|---|
| Default surface | `/new` via `--converse --no-uuid` (`--no-uuid` alone invalid — 24611) |
| Forbidden | Endeavor Cowork Project UUIDs unless operator explicitly binds |
| Endeavor chrome map | `cortex://notes/system/threads/5129-project-chrome-map.md` — ¬ default R-admit surface |

## Parallel Chrome — registry detail (BINDING)

**DEFAULT — register a lane.** Registry SOT: `libs/claude_bundles/cdp_registry.py` + `~/.gateway/cdp-registry/`.

### Simultaneous lane capacity (a:25814)

SOT: `libs/cdp_ask/execution_store.py` `active_work_snapshot()`.

| Field | Meaning |
|---|---|
| `busy` | Restart-drain only — derived `effective_count > 0` — **¬** lane-full |
| `running_count` | In-flight executions (`AuthorityClass.RECORDED`) |
| `live_cse_count` | Observed live CSE pages (`AuthorityClass.OBSERVED`) |
| `effective_count` | `max(running_count, live_cse_count)` — admission + restart-drain |
| `soft_limit` / `hard_limit` | 2 / 3 |
| `free_slots` | `max(0, hard_limit − effective_count)` |
| `at_soft_limit` / `at_hard_limit` | `effective_count >= soft_limit` / `hard_limit` |

**Recorded-only vs effective (BINDING):** `list_capacity()` counts registry rows with
`status == active` only — recorded lanes. `busy_status` / `/active-work` use
`effective_count` because a live CSE without a recorded project-ask execution still
blocks restart drain (Cowork keeps the life MCP connector hot between tool POSTs) and
consumes admission headroom. Do **not** derive `free_slots` from `running_count` alone.

```
register_lane(holder) → (registration_id, port, profile_suffix, cdp_url)
pool = 9223–9349 (:9222 excluded — attended primary)
--no-register --cdp-url http://127.0.0.1:9222 = attended primary contract
∀ peer.port : ¬pkill(peer)   # kill only exact listener pid
∀ port ≠ 9222 : require(--profile-suffix)
```

### Orphan scan — closable/protected observability (emit-only, S1)

SOT: `libs/claude_bundles/cdp_orphans.py` `orphan_scan_as_dict()` ·
`cdp_orphan_cse_classify.py` · event `cdp.port.orphan_scan`.

Each orphan scan emits observation counts; classifications are **scan-ephemeral**
(not persisted on registry rows; S3 reaper consumes fresh dicts when reclaim is enabled):

| Field | Meaning |
|---|---|
| `closable_count` | CSE targets classified `closable` (idle past dwell; safe to close) |
| `protected_count` | CSE targets classified `protected` (streaming, attach unresolved, probe fail-closed) |
| `cse_classification` | Always `"scan_ephemeral"` in dict output |
| `reclaim_enabled` | Always `false` until S3 flag ON |

Per-orphan `matched[]` entries carry per-target `classification` on `cse_targets[]`.
Event payload mirrors aggregate `closable_count` / `protected_count` /
`reclaim_enabled: false`.

## OptGuide profile disk (BINDING — friction 25050)

Standing fix **`S0_lane + S2 + S4`** (`decision:cdp-optguide-s0-s2-s4-bind`).

| Piece | Invariant |
|---|---|
| **S2** | `_seed_profile` rsync **excludes** `OptGuide*` and `optimization_guide_model_store` |
| **S0_lane** | `--disable-features=OptimizationGuideOnDeviceModel` (lane argv only) |
| **S4** | `hygiene_reclaim_released` frees ports **and** `rmtree`s released profile dirs when safe |

Friction: `cortex://notes/system/friction/25050-cdp-optguide-profile-accretion.md`.

## Prompt-file path contract (BINDING — friction 24951)

`claude-ai-sync-jupiter project-ask` runs on **Jupiter via SSH** — `--prompt-file` paths passed verbatim; **no rsync**.

```
∀ project-ask --prompt-file PATH:
  PATH readable on Jupiter before register_lane()
  ¬ seat-local /tmp on Cursor (wrapper MUST reject /tmp before SSH)
  Convention: durable sealed prompts → tmp/reviews/<name>.md
  Relative paths must resolve inside PROJECT_ROOT
```

## On-behalf `unread_turns_exist` (BINDING — landed 5737)

When CDP posts on-behalf and bus returns **409 `unread_turns_exist`**, remake after-turn pointer from `latest_turn_number` and **retry once**. Second failure → fail-closed **DELIVERY FAILED**. Code: `services/universal-stargate/systems/frontier_consult/cdp_generate_worker.py`.

## Entry points — full matrix

| Job | Command |
|---|---|
| **Product — team_dispatch (DEFAULT)** | `team_dispatch(op=generate, model=cdp/opus-5\|cdp/fable, contract=light-bounded, prompt\|sidecar_ref=…, dispatch_thread_id=…)` → `agent_bus.wait` from `poll_hint` |
| **Escape — MCP project_ask** | `project_ask(op=submit, prompt_uri=cortex://…, converse=true, no_project_uuid=true, model=opus-5\|fable-5)` → poll (when team_dispatch CDP unavailable / satellite-direct) |
| **Operator-proxy mission** | `team_dispatch(model=cdp/opus-5, purpose=operator-proxy\|mission, …)` primary; `project_ask(…, purpose=…)` escape |
| Path-sim R-admit (CLI fallback) | `… project-ask --register --purpose ask --converse --no-uuid --model opus-5 --prompt-file tmp/reviews/…` |
| Long task / multitask | Default Cowork (omit flags) |
| Auto lane (Fable) | `… project-ask --register --purpose fable --converse --no-uuid --cowork-auto --model fable-5` |
| Register / list / deregister | `… register-lane` / `list-lanes` / `deregister-lane` |
| Bound Project ask | `… project-ask --uuid <explicitly-bound>` — only when Project named |
| N-turn `/new` | `… project-ask --converse --no-uuid --model fable-5 --prompt-file t1.md … [--close]` |
| Operator Chat on `/new` | `--chat` · MCP `chat_compose=true` |

## MCP `delete_after` (retain vs delete)

| Path | Omit `delete_after` | Explicit retain | Explicit delete |
|---|---|---|---|
| **Converse** (`converse=true`) | Chat **retained** | `delete_after=false` | `delete_after=true` |
| **Single ask** (`converse=false`) | Chat **deleted** after harvest | `delete_after=false` | `delete_after=true` |

CLI parity: `--keep-chat` ≡ `delete_after=false`.

## Warm follow-up — same CSE window (BINDING — 2026-07-31)

**Receipt ladder (v1 live rungs):** `dom_paste` (composer empty of the needle, plus marker in composer-excluded committed-turn nodes or count growth + snippet) → `dom_committed` (marker survives a short settle in committed user-turn nodes — **not** `page.reload()`, which wipes unsent drafts). DOM on an automation-attached page proves **satellite-scope** rendering — never server commit, never attended-session visibility. Reserved (not implemented): `attended`, `server_transcript`, `human_visible`.

```
∀ follow-up on retained /cowork/cse_* ∨ /chat/* URL:

  Primary (IDE MCP):
    project_ask(op=followup,
      chat_url=… | registration_id=… | execution_id=… | identity omitted,
      cdp_url=…,  # explicit (cdp_url, chat_url) override with chat_url
      prompt_text=… | prompt_uri=…,
      purpose=operator-proxy,
      timeout_s=60,
      min_receipt=dom_paste | dom_committed)  # default dom_paste
    → paste proof (send_verified, receipt, target_binding); no reply harvest
    Identity omitted ⇒ attended resolve-or-refuse (no_identity terminal deleted)
    Identity ladder when supplied: chat_url ≻ registration_id ≻ execution_id
    v1 = attached lane only

  Read-only attended triple:
    project_ask(op=resolve_attended)

  Escape (hub checkout / no attached lane):
    scripts/cortex/cowork_chat_followup.py
      [--cdp-url http://127.0.0.1:<port> --chat-url <exact URL>]
      --prompt-file <Jupiter-readable path>
    (flags omitted ⇒ same attended resolver as satellite)

  Launch-path (reattach mints lane): paste proof is satellite-scope only
  ¬ relay ok=true / send_verified=true as human/CSE delivery (a:27855)
  ¬ project_ask(op=submit, … /new) for turn on retained CSE
```

Gap closed 2026-07-31 — IDE warm paste via `project_ask(op=followup)`; CLI = escape/dogfood only.

### IDE → live operator-proxy CSE (BINDING)

```
∀ wake | correction | ladder-fix to retained operator-proxy CSE:
  deliver IN CHAT via warm follow-up (project_ask op=followup)
  ∧ bus turn may accompany as audit
  ¬ bus-only + operator push reminder
```

## Model lanes

**SOT = live CDP picker** (a24691). `select_model` may try `PREDICTED_MODEL_LABELS` first; on miss discovers radios. Harness must not treat prediction list as whitelist.

| Model | Use |
|---|---|
| Opus 5 + Effort **High** | Default sealed-ask / plan |
| Opus 5 Extra | Explicit only (`opus-5-extra`) |
| Opus 5 Max | Ceiling (`opus-5-max` / effort `max`) |
| Fable 5 High | Protocol consult (n-turn; keep until `--close`) |
| Sonnet (live UI label) | When picker offers it |
| `leave` | Do not touch picker |

**Tier-vocab Max == picker Max** — not Extra (24727).

`Fable ⇒ CDP picker ∨ web-anthropic` — ¬ API `team_dispatch(… anthropic/claude-fable-5)`.

**Anthropic-family substrate (`decision:anthropic-family-dispatch-substrate`):**

| Path | Default |
|---|---|
| Stargate `anthropic/*` API | **PROHIBITED** |
| `cursor/*` (cursor-sdk) | **OK** except **Fable** |
| Opus / Sonnet / web consults | **CDP preferred** — cortex-packaged corpus |
| Live checkout navigation | **`cursor/claude-opus-*`** acceptable |

## Skill delivery detail

**Named split — inject vs RAG (BINDING):**

| Class | What | How |
|---|---|---|
| **Inject artifacts** | Gate / charter / sealed task body | Packet / `fs` / explicit inject |
| **RAG context** | Corpus priors | `rag(op="search", mapped=true\|false)` by executing agent |

**Skill delivery on Cowork (MUST — friction 24594):**

```
¬ load(skill_tree via GitHub)
skill_tree ∈ {.cursor/skills/**, .claude/skills/**, .* gitignored}
```

| Need | Lawful channel | Forbidden |
|---|---|---|
| `cursor_only` skill | Local inject into prompt/sidecar | GitHub path · Customize Skills |
| `shared_sync` / `life_local` | **+ → Skills → pick** or local inject | Claiming GitHub loaded SKILL.md |

Mechanics: `libs/claude_bundles/cowork_skill_delivery.py` + `composer_session_skills.py`. Handoff: `cortex://notes/system/threads/cdp-skill-attach-plus-skills.md`.

**Packet-class required sets (minimum):**

| Packet class | Claude-slug engage | Inline (not Claude slugs) |
|---|---|---|
| Light-bounded architect / admit bind | `reasoning-posture` (+ `consult-posture` when consult-shaped) | none — **¬** `path-sim` unless this leg is a path-sim Q/A/R cascade (a:27142) |
| **`/layer` G1 · Fable/Opus architecture** | optional judgment chips | **`architecture-invariants` + `ulg-architecture`** (fail closed — judgment chips ¬ substitute; lead preflight in `abstraction-layering`) |
| **ULG service home / placement / extract / hosting BIND** | `reasoning-posture` (+ `consult-posture` when consult-shaped) | **`architecture-invariants` + `ulg-architecture`** — and **inline** `[ulg:host-process]` when process manager / service home is load-bearing (cursor_only → local inject / excerpt) |
| Modularize / overhaul deep split | optional chip helpers | `architecture-invariants` + `modularize-discipline` (+ `ulg-architecture`) — `/modularize` cascade SOT: `modularize-path` (CDP M-Arch fail-closed inline) |
| Overhaul §5.6 / `/docstring-enhance` CDP | `evidence-review-discipline`, `no-silent-inference` | short `architecture-invariants` floor |
| Overhaul step-4 deep code review | `evidence-review-discipline`, `reasoning-posture`, `no-silent-inference` | `architecture-invariants` + `ulg-architecture` |
| `/review-arch-doc` CDP | same three as step-4 | `architecture-invariants` + `ulg-architecture` |
| Path-sim R-admit | `reasoning-posture` + `consult-posture` | none required by default |

**Hosting load-bearing (BINDING — friction class: poisoned BEFORE-map systemd framing):** when the packet asks where a **service lives**, how it is **extracted**, or which **process manager** owns it, the sealed prompt MUST include a one-line inline of `[ulg:host-process]`: except satellites, host fleet = `./manage` subprocesses; repo `systemd/*.service` ≠ live path. Judgment chips alone do not substitute for the architecture floor on this class (agent-bus:6301).

**Anti-patterns**

| Bad | Good |
|---|---|
| Packet cites `workspaces://…/SKILL.md` as readable | `cortex://notes/…` URIs + life MCP `fs` |
| "Use GitHub to browse `libs/`" | Paste diffs / stage under cortex |
| Slash life_local/cursor_only skill names | Inline excerpts |
| Pass `path-sim` on light-bounded architect / admit binds | Judgment pair (+ `consult-posture`); `path-sim` only on Q/A/R cascade legs |
| Lead `rag` + merge into expand when MCP `rag` available | Executing agent calls `rag` via MCP |
| Type multiple `/slug` lines expecting chip bind | Manifest + `attach_session_skills` (+ → Skills) |
| Omit unattended clause from sealed / charter R packets | L2 § Sealed / unattended |
| Treat slash-manifest lines as skill delivery | Attach + channel attest before submit |
| Architecture placement packet with only judgment skills; systemd framing from BEFORE map unchallenged | Attach/inline `ulg-architecture` + `architecture-invariants`; inline `[ulg:host-process]` when hosting is load-bearing |
| `/layer` Fable G1 with judgment chips only (no `ulg-architecture`) | Seal arch pair; lead preflight check before admit (`abstraction-layering`) |

## Hygiene (5195)

1. `context_fat ⇒ noise` — fresh chat per disposable ask
2. `delete ⇒ often /new` — re-enter Project URL before next send
3. `harvest(Project shell) ⇒ n=0` — prefer `/chat/`
4. Strip `Thinking about…` lines
5. Durable = Cortex sidecar; chat disposable **after** archive

## Settled gate (Fable binding amendments)

Lane **"settled"** iff all hold + transcripts archived:

1. Completion predicate includes error_banner / turn_increment / ¬tool_pause — skill **and** code
2. Archive-before-delete enforced; success prints archive URI
3. Per-turn model attestation on harvest
4. CLI ask/converse delete semantics by command identity
5. Timeout + cookie-staleness with capture∧¬delete∧friction
6. **Falsifier transcripts:** forced incomplete + error/limit + validation→refuse-delete

**Status 2026-07-16:** gates 1–6 **PASS** — `cortex://notes/system/threads/4917-fable-cdp-review/falsifiers/SUMMARY.json`.

## Non-goals

- ¬ replace agent_bus for attended web-anthropic
- ¬ authority-launder ("Fable said" from unattested harvest)
- ¬ general claude.ai UI automation framework
- ¬ second SOT for selectors (libs own mechanics)
- ¬ load skill trees via GitHub connector (gitignored — 24594)
- ¬ host-wide Chrome GenAI policy / PRIMARY OptGuide deletion as CDP disk SOT (25050)
