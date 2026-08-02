# Path-sim L3 annex — dispatch cascade (Q → A → R-admit → implement → R-after)

**Parent SOT:** `.cursor/skills/path-sim/SKILL.md` (this plugin path: `cursor-plugins/ulg-ecosystem/skills/path-sim/SKILL.md`).
L3 mechanics: command surface, phase table, recon, R positions, operator-framed Q, Q-only / Q-cascade, bundled dispatch, Gate-2 densify closeout, R-admit CDP recipe + poll ladder, auto-advance checklist, Stage-B, anti-patterns, todo lifecycle bind.
Open this annex when **orchestrating** an arc. A path-sim *consult turn* (reasoning only) needs L2 alone.

## Closed-detent quick recipe (single light consult — ¬ the bundled arc)

For a well-scoped fix whose loci are already known (the 2026-07-21 fleet-drain
sketch class): a **single light Grok consult**, not the full Q→A→R bundled arc.
Copy this — do not re-derive routing each time:

1. **Scope-lock** (4 fields, § handshake) — Question / Out-of-scope / Good-answer / Origin.
2. **Thin L2 fill** — 2–3 *decorrelated* rival binds + one-line research-anchor gloss (lexical spark).
3. **Single-seat dissent beat** (one line) — steelman the runner-up before bind. ¬ N-way debate.
4. **Bind** — recommended patch locus + falsifier.

**Transport (pinned — code lane):**
`team_dispatch(op=generate, seat=cursor-sdk, model=cursor/grok-4.5, contract=light-bounded, effort=low)`
— ¬ `xai/grok-*` (checkout present), ¬ `anthropic/*` API (§ substrate house rules).

**Substrate preflight (before firing):** confirm the delivery chain is up
(`manage(busy_status)` — `agent_bus` + `stargate`); a partial/aborted fleet state
silently breaks the dispatch transport (`advisor-timing` Checkpoint 0.6). When the
consult subject *is* a service-lifecycle bug, that bug may have downed the transport —
restore your own dependencies first.

**When this suffices (vs bundled `/path-sim`):** `|material_sub_parts| ≤ 2` ∧ loci
pre-selected ∧ ¬ architecture-suitability in scope. Escalate to the bundled arc
(R-admit + R-after) the moment a rival bind touches an invariant or the fix is not
self-verifiable.

**Carve-out vs default Q seating (operator 2026-07-28):** this closed-detent recipe
is **Grok-only by design** — it is **not** the bundled arc and does **¬** force CDP
Fable Q. Bundled / full-arc default Q = CDP Fable (§ Dispatch bindings).

## Friction conveyor mint triage (fleet — automatic)

Charter harvest mints follow-on todos with `dispatch_lane=path-sim-admit-gate` and
stamps **`detent`** via `classify_friction_detent` (`libs/cortex_store/dispatch_ops/_friction_detent.py`):

| Detent | When | Admission packet |
|---|---|---|
| `closed` | suggestion present ∨ concrete loci (paths / ``Fix:``) ∧ ¬ architecture language | closed-detent thin recipe (skip G3/G5 unless escalate) |
| `standard` | actionable but loci not pre-selected | full autonomous path-sim arc |
| `wide` | architecture / rival / cross-agent / invariant language | full autonomous path-sim arc |

Conveyor Next-pickup carries `detent=…`; autonomous `select_packet` routes on that
token. Mid-window escalate: CHECKPOINT with raised detent + STOP.

## Command surface (wraps the skill)

**Invariant:** the skill is **SOT** for machinery **and** dispatch bindings. **Sole command-layer entry** is `.cursor/commands/path-sim.md` — a thin wrapper (When + lead obligations). Commands defer to the skill for cascade/transport — ¬ re-derive in command prose.

| Command | Role |
|---|---|
| `/path-sim` | Sole entry — friction / todo / assertion → § Dispatch bindings. **Default for `judgment_required` = bundled** (§ Bundled dispatch). Mid-cascade may run Q-only then A/implement separately. |
| `/path-sim-address` | **Retired alias** — redirect stub only; same as `/path-sim` bundled. ¬ fork contracts there. |
| `/work-item-review` | **R-after** entry — post-ship R over a work item's delivery (§ R positions). ¬ a second path-sim arc. |

### Entry invocations

```
/path-sim friction {assertion_id}   # → bundled by default
/path-sim todo:{slug}               # judgment_required ⇒ bundled
/path-sim a:{assertion_id}
/work-item-review todo:{slug}       # R-after (post-ship) — § R positions
```

## Dispatch bindings (Cursor default)

**Lead seat (Cursor Auto / current lead model):** orchestrates the whole arc — pick recon executor, fire **Q (CDP Fable)** then dispatch A/implement, fire R-admit **and** R-after, adjudicate sidecars, auto-advance. Lead **≠** the default recon *worker* and **≠** the A/implement worker. **¬** in-seat L0/L1/L2 reasoning or hand-implement on `judgment_required` arcs unless operator explicitly overrides (`authority_fork`, attended consult).

**Default cascade** (friction-minted todos carry `dispatch_lane=path-sim-admit-gate` on **cursor-sdk** admits and todo attrs only — operator bind 2026-07-28; **¬** on `team_dispatch(model=cdp/…)` — CDP legs use `(model, contract, dispatch_thread_id)`):

```
recon → Q (lead CDP Fable L0) → A (cursor-sdk Grok L1+L2 + bind) →[halt] R-admit (lead CDP web-anthropic Opus, default-on)
  →[ADMIT] implement (Composer) → R-after (/work-item-review · cursor/grok-4.5, default-on) → closeout
```

Order is binding: **recon then Q** (soft gate — ¬ invent a hard RAG/Tier-1 blocker; see § Recon). Closed-detent quick recipe (§ above) stays Grok-only and is **not** this cascade.

R-admit and R-after are the **same R posture** at two timeline pins (§ R positions) with **split substrates** (operator bind 2026-07-21; A→Grok 2026-07-22; **Q→CDP Fable 2026-07-28**):

| Pin | Substrate | Why |
|---|---|---|
| **R-admit** | web-anthropic CDP · **Opus 5** | Cross-weight-class pin vs Q (Fable) and vs A (Grok); staged corpus is enough for bind critique — Q and R must **not** be the same seat |
| **R-after** | **`cursor/grok-4.5`** · `seat=cursor-sdk` · `contract=light-bounded` | Delivery critique needs **live checkout** (`files_expected` ∩ ship); prefer packaged `cortex://` hot paths for speed, but `workspaces://` is readable on web when exploration is named. Independence trade: same family as A, **≠** Composer implement — document; R-admit remains the cross-family / cross-weight pin |

Both pins **default-on** for bundled `judgment_required` arcs — skip only the closed set (`check_requested=false` / operator no-check, or transport unavailable: CDP down for R-admit / cursor-sdk unavailable for R-after). R-admit cannot see the ship; R-after is the delivery half (acceptance ledger, drift, docstring scan, event-instrumentation challenge).

| Phase | Executor | Model (post-Fable window) | Sidecar |
|---|---|---|---|
| 0 Recon | **Orchestrated by lead** — pick executor = in-seat **or** `team_dispatch(seat=cursor-sdk, contract=light-bounded)`; adjudicate sidecar. Lead MAY keep thin Tier-1 greps in-seat when cheap; default posture for non-trivial breadth = **dispatch** (lead stays orchestrator, ¬ conflate lead≡recon worker). `rag(op=recon)` optional. | **Dispatched:** pure mechanical inventory → `cursor/composer-2.5`; investigate/judgment/root-cause → `cursor/grok-4.5` (¬ default Composer on investigate). **In-seat (narrow):** current lead model when lead elects not to dispatch. | `cortex://notes/system/recon/{slug}/…` (Tier-1 anchors required when breadth/unknown locus) |
| 1 Q (L0) | **Lead fires CDP Fable** — default bundled/full arc. Primary: `team_dispatch(model=cdp/fable, contract=light-bounded, …)` (Use the `claude-ai-cdp-navigation` skill · consult-routing Anthropic substrate). Escape: `project_ask` / CLI with `model=fable-5`. Operator-framed only via **positive attestation** (`operator_framed` + `pinned_question` + resolvable `frame_uri`) ⇒ **bounded adopt-or-contradict Q** (`frame_verdict` + `frame_delta`) then A — **¬** `q_skipped`, **¬** frame-as-Q. Unframed/isolated ⇒ normal **Fable Q** → Grok A — ¬ escalate to human (§ L0 / Q pairing). **Downgrade Q to Grok** only under closed detent (§ Closed-detent quick recipe) or explicit operator skip. **¬** default Q to Opus CDP (R-admit owns Opus — keep Q≠R seats). | Fable Max (CDP) | `cortex://notes/system/threads/path-sim-{slug}-fable-l0-q.md` |
| 2 A (L1+L2) | **`team_dispatch(op=generate, seat=cursor-sdk, model=cursor/grok-4.5, contract=light-bounded, …)`** — **halts at admit-gate, ¬ implement** | Grok-4.5 High | `…/path-sim-{slug}-grok-a-l1l2.md` |
| 3 R-admit | **LEAD fires CDP `project-ask` bus-nudge** (Use the `claude-ai-cdp-navigation` skill) | web-anthropic **Opus 5** | **default-on, lead-owned** — skip only closed set |
| 4 Implement | **`team_dispatch(op=generate, seat=cursor-sdk, contract=implement, source_ref=todo:{slug})`** — **separate dispatch, after R-admit ADMIT** | cursor-sdk Composer 2.5 (role default) | code diff + closeout sidecar |
| 5 R-after | **LEAD fires `/work-item-review todo:{slug}`** via **`seat=cursor-sdk, model=cursor/grok-4.5`** — after Stage-B ship | **Grok-4.5 High (cursor-sdk)** | **default-on, lead-owned** — same closed skip set; entry SOT = `.cursor/commands/work-item-review.md`. Checkout-native delivery critique (≠ R-admit web seat). |
| 6 Closeout | lead (orchestrator) | — | `…/path-sim-{slug}-implement-closeout.md` (+ R-after verdict URI) |

### Recon (phase 0) — orchestrated; Tier-1 durable; RAG optional

SOT for the ladder: Use the `cheap-recon-before-escalation` skill. Path-sim does **not** invent a separate recon doctrine.

**Lead ≠ recon executor (by default).** Lead **orchestrates** phase 0 (pick in-seat vs dispatch, adjudicate sidecar). Non-trivial breadth ⇒ prefer `team_dispatch(seat=cursor-sdk, contract=light-bounded)` with model split below — keep lead context lean. Narrow one-shot greps MAY stay in-seat. “Lead = Auto” names the **orchestrator seat across the arc**, not “Auto hand-runs Tier-1.”

```
path-sim phase 0 ≡ durable Tier-1 anchors sidecar when breadth / unknown locus
rag(op=recon, durable_sink=cortex) ≡ optional iff a named corpus scope is known to cover the concern
¬ RAG ⇒ ¬ block Q dispatch
¬ (lead seat ≡ recon worker) — orchestration ≠ authorship
```

| Do | Don't |
|---|---|
| Dispatch mechanical inventory → Composer; investigate → Grok (`cheap-recon` / consult-routing split) | Equate “lead runs path-sim” with “lead hand-runs all greps” |
| Greps, source reads, Event Service gaps → `cortex://notes/system/recon/{slug}/tier1-anchors.md` (or theme) | Treat `rag(op=recon)` as a Q-gate |
| Call RAG when the domain is actually indexed for the question (research / known scope) | Cargo-cult RAG on code-local Stargate/MCP frictions when Tier-1 already pins loci |
| Discard noisy RAG hits in `## Discards` and bind on Tier-1 | Equate “path-sim without RAG” with incomplete cascade |

Dogfood (2026-07-18, `todo:packet-ac-l0-planning-rubric` / a:25086): RAG returned research PDFs; Tier-1 code anchors carried the bind.

**R is lead-owned (BINDING).** CDP `project-ask` needs the Jupiter Chrome lane + profile, which exist **only on the lead cursor IDE seat** — the headless cursor-sdk worker sandbox HOME has **no** CDP transport. Therefore **R MUST NOT be a phase inside the cursor-sdk worker packet**; a worker asked to "run R" can only self-certify (the 2026-07-18 dogfood defect). The lead fires R between the A dispatch and the implement dispatch.

### R ≠ skeptic ≠ Gate-6 (do not substitute)

These are **three surfaces** — conflating them is a cascade defect (dogfood 2026-07-17 pipeline-viewer arc):

| Surface | What | Who | Satisfies |
|---|---|---|---|
| **Path-sim R** | External check of the A-bind (scope-lock `RATIFY\|REVISE\|SCOPE-DRIFT`) | **web-anthropic via CDP** (lead-owned) | Path-sim phase 3 only |
| **Axis-2 panel** | Adversarial design ratification | **code lane: `cursor/*` on `seat=cursor-sdk`** · non-code / checkout-free: API `role=reviewer` + `role=skeptic` | `skeptic_ratified` / Gate-6 when `check_requested` |
| **Gate-6 bypasses** | Alternate admit proofs | `gate6_ratification_uri` or hash-matched `recon_waived` | Implement admission — **¬** path-sim R |

**Panel substrate on code (operator 2026-07-26).** A review pass **on code** defaults to
`cursor/*` on cursor-sdk — that substrate holds the live checkout, which is what makes a
code review effective. API roles are reserved for **architecture / bird's-eye** reasoning
and checkout-free self-contained recon; CDP web-anthropic keeps R-admit. `openai/*` is
metered and is **not** the code-review default; `xai/*` MCP support is weak. Where any
routing surface would send a **code** review to API, default to CDP or `cursor/` instead.
Doctrine: `decision:code-review-panel-cursor-substrate`; lane mechanics: `consult-routing`
§ Code vs non-code (`dispatch_lane`) and § Gate-6 substrate.

`¬ stamp recon_waived / skeptic_ratified / lead self-ADMIT as a substitute for CDP R.` Lead confidence that a bind is "mechanical" is **self-signal** — R exists to challenge that claim.

### R positions (same semantics, two timeline pins)

R is one posture (scope-lock grammar · `RATIFY|REVISE|SCOPE-DRIFT` · external verifier · ranked dispositions). It appears at **two timeline positions** — ¬ two inventions:

| Position | Entry | Timing | Question pinned by | Substrate |
|---|---|---|---|---|
| **R-admit** | `/path-sim` phase 3 (lead CDP) | *before* implement | A-bind / cascade scope-lock | web-anthropic · Opus 5 |
| **R-after** | `/work-item-review` — **default-on after path-sim Stage-B** | *after* ship | work-item `acceptance_criteria` + `files_expected` | **`cursor/grok-4.5`** · cursor-sdk |

Same R semantics live in the parent skill. `/work-item-review` owns after-ship timing + charter-scoped file derivation + **R-after substrate bind**; it defers disposition/falsifier/reviewer-rule grammar to path-sim. Reflect-axis doctrine (external PRM vs self-signal; G4/G5) lives in `expand-growth-loop_ws.mdc` — ¬ restated here.

**Default:** bundled path-sim arcs fire **both** pins on the split substrates above. R-admit alone is an incomplete external check — implement can drift from the dense-spec AC; docstring criticals, event-instrumentation judgment, and charter drift are only observable after ship. Skip either pin only under the closed set. Manual `/work-item-review` remains valid for non-path-sim work items (same Grok default unless operator overrides).

**Docstring in review (BINDING — two pins, not one):**

| Pin | Docstring duty |
|---|---|
| **R-admit** | Challenge only: if bind adds public surface ∧ dense-spec `acceptance_criteria` omits docstring conformance → `ADMIT_WITH_AMENDMENTS` or `RETURN`. ¬ scan (nothing shipped yet). |
| **R-after** | Run `scripts/docstring-quality scan|check` on `files_expected` (touched public surface). **criticals=0** or `RETURN` / amend. Cite scan path + exit in review sidecar. Concentrated warnings that starve arch feedstock → note lead `/docstring-enhance` (CDP) before close. |
| Lead closeout | Same criticals=0 gate as R-after (primary ship gate); R-after is the external-review pin when `/work-item-review` runs. |

**Event instrumentation in review (BINDING — write-time + R-after; ¬ R-admit table):**

| Pin | Event-instrumentation duty |
|---|---|
| **Write-time (Stage-B)** | Use the `event-instrumentation-discipline` skill — log→event / prune judgment while authoring. |
| **R-admit** | n/a for opportunity-find (nothing shipped). ¬ reinstate Event Coverage harvest. |
| **R-after** | When `$ON_CHARTER` touches behavioral edges or `@event_factory` sites: challenge closeout one-liner (events added · "no event warranted (reason)" · prune candidates) **and** flag missed log→event / hot-signal prune on delivered code. Judgment findings — ¬ criticals scan, ¬ Event Coverage table. |
| Lead closeout | Same one-liner obligation as implement-todo §5; R-after is the external challenge pin. |

### L0 / Q pairing + operator-framed Q (BINDING — operator 2026-07-26 · amended agent-bus:5964/5966)

**Pairing invariant (P1):** path-sim always runs **Q∧A as a coupled unit** — never A without a Q sidecar/verdict, never Q without a following A. The operator frame is **input to Q**, never a substitute for Q (P2 rejected — destroys the falsifier).

**Default executor for path-sim Q is CDP Fable** (`team_dispatch(model=cdp/fable)` / `project_ask` `fable-5` escape — Use the `claude-ai-cdp-navigation` skill; ¬ `anthropic/*` API). Rationale (operator 2026-07-28): Fable owns explore / L0 width; **R-admit stays Opus CDP** so Q and R are not the same seat; **A stays `cursor/grok-4.5`**. Do **¬** default Q to Opus CDP. Do **¬** default Q to Grok on the bundled arc — Grok Q is the **closed-detent / explicit-skip** carve-out only. A strong frame makes Q **cheap** (bounded adopt-or-contradict), not **absent**.

Vision / architecture-suitability framing belongs on the **operator seat**, which must engage `reasoning-posture` **and** `frontier-reasoning-discipline` (pin Question · Out-of-scope · detent ≺ widen; then steelman / calibrate) so it sees further and wider — Use the `cdp-operator-proxy` skill § Invariants. Path-sim then **tests** that frame when attested (falsifiable feedback), ¬ rubber-stamps it.

#### Detecting operator-framed (positive attestation only)

**Absence is not a signal.** Isolated path-sim (friction pickup, IDE-led todo, no tick/DIRECTIVE trail) looks the same as "operator never framed" — do **¬** infer a missing frame from empty fields, and do **¬** escalate to the human operator to supply one. Unattested ⇒ treat as **unframed** and continue the cascade (**Fable Q** → Grok A).

Operator-framed is true only when **all** joint stamps exist on the work item (Opus bind 5966 — neither alone sufficient):

| Signal | Where |
|---|---|
| `attributes.operator_framed=true` | todo (preferred durable stamp) |
| Non-empty `attributes.pinned_question` authored/stamped by operator seat | todo |
| Resolvable `attributes.frame_uri` (or op-lane body URI) pointing at the frame prose | todo / cortex |
| DIRECTIVE / tick cites the pinned Question + `evidence_uris` includes `agent-bus:{op-thread}#turn-N` | todo attrs / assertions |

Operator duty when seeding ticks / DIRECTIVEs that path-sim will consume: **stamp** `operator_framed=true`, `pinned_question`, and `frame_uri` (after reasoning-posture + frontier-reasoning-discipline); **one Question per tick**. Optional: `repos[]` when work spans satellites, or positive `satellites: none` when hub-only — path-sim must **¬** assume hub-only checkout. Unstamped work is isolated by definition.

| Attestation | Path-sim Q → A |
|---|---|
| Operator-framed (joint stamps above) | **Bounded adopt-or-contradict Q on CDP Fable** — fetch `frame_uri`, test against recon, emit `frame_verdict` ∈ {adopted, contradicted, sharpened} + `frame_delta`, hand A the pinned Question — **¬** `q_skipped`, **¬** thin-confirm-as-skip, **¬** re-buy Opus CDP Q (R-admit owns Opus) |
| Unframed / isolated (no positive stamp) | **Normal CDP Fable Q → Grok A** — ¬ escalate to human; ¬ block on missing operator frame |
| Routine / lead-pre-pinned (non-operator) | Thin Fable Q (or Grok under closed detent) → A |

Default when Question is pre-pinned by a non-operator lead: still **dispatch thin Q** (§ Q-only) — confirm / kill / sharpen sub-Qs; write the Q sidecar.

| Allowed | Forbidden |
|---|---|
| Stamp `operator_framed=true` + `pinned_question` + `frame_uri` (obliges a Q verdict) | Inferring "operator framed" from absence of isolation markers |
| Framed ⇒ adopt-or-contradict Fable Q then A | `q_skipped=true` / thin-confirm that **skips** Q / treating frame as Q |
| Unframed ⇒ Fable Q → Grok A without paging the human | Defaulting bundled Q to Grok (use closed-detent recipe or explicit skip instead) |
| Closed-detent / explicit operator skip ⇒ Grok-only light consult | Collapsing Q and R-admit onto the same Opus CDP seat |
| Positive `satellites: none` or explicit `repos[]` | Halting unframed arcs to "escalate to operator" / wait for a frame |

**¬ skip A dispatch.** Unframed never means "skip A" or "wait for the operator." Framed never means "skip Q."

**Two-stage, not one worker:** `/path-sim` on fresh `judgment_required` pickup = **lead-orchestrated** bundled arc: **recon → lead CDP Fable Q** → worker Stage-A (A + Gate-2 → **halt**) → **lead CDP R-admit** (web-anthropic Opus) → worker Stage-B (implement) → **lead fires R-after** (`/work-item-review` · `cursor/grok-4.5`) → lead closeout. "Bundled" = the lead auto-advances the stages without operator "go" — **not** one cursor-sdk dispatch spanning Q or R-admit. Mid-cascade may run Q-only then A/implement separately — still off-seat.

### Q-only dispatch (phase 1)

Six-block packet at `tmp/prompts/path-sim-{slug}-fable-q-packet.md` (or staging URI under `cortex://notes/system/threads/…`). Scope-lock Question = meta question-space OR operator-pinned decision. `<task_guidance>`: L0 only — ranked question table (`ask-now/defer/kill`) + one recommended Question set; **¬ L1/L2**.

**Primary (preferred when `cdp/` model-endpoint is live):**

```
team_dispatch(
  op=generate, model=cdp/fable, contract=light-bounded,
  sidecar_ref=cortex://notes/system/threads/path-sim-{slug}-q-prompt.md,
  # or prompt=… when short
  dispatch_thread_id=<bus thread id>,
)
# poll: agent_bus.wait from poll_hint (from_agent=cdp)
```

**Escape — MCP `project_ask` / CLI** (same sealed `/new` posture as R-admit recipe; Use the `claude-ai-cdp-navigation` skill):

```
project_ask(
  op=submit,
  prompt_uri=cortex://notes/system/threads/path-sim-{slug}-q-prompt.md,
  converse=true,
  no_project_uuid=true,
  model=fable-5,
  purpose=ask,  # or purpose=fable when Cowork Auto lane applies
)
```

**Downgrade carve-out (closed detent ∨ explicit operator skip only):**

```
team_dispatch(
  op=generate, seat=cursor-sdk, model=cursor/grok-4.5,
  contract=light-bounded,
  dispatch_thread_id=<bus thread id>,
  packet_path=tmp/prompts/path-sim-{slug}-grok-q-packet.md,
  skills=[path-sim, frontier-reasoning-discipline, cursor-sdk-instruction-standard]
)
```

Write sidecar to `cortex://notes/system/threads/path-sim-{slug}-fable-l0-q.md` (or `…-grok-l0-q.md` under the downgrade carve-out); log path-sim conformance checklist **and the `Q-CASCADE` footer** (§ Q-cascade) in sidecar footer.

### Q-cascade — default Fable Q; optional Grok downgrade / recovery sharpen

**Default Q is already CDP Fable** (operator 2026-07-28). The old `escalate-fable` opt-in (Grok self-decides → lead fires Fable) is **retired as the default path** — Fable Q is on by default for the bundled arc.

When Q was intentionally **downgraded to Grok** (closed detent or explicit operator skip) and the Grok L0 worker self-assesses that the question space still warrants a wider Fable sharpen, emit a **`Q-CASCADE`** verdict in the sidecar footer — the lead honors it by firing CDP Fable as a **recovery sharpen** (same transport as § Q-only). There is **no opus/fable choice** on that hop (Fable is the sole sharpen target while the window holds; Opus Max only when Fable is unavailable — same substrate row as § Per-family parameters).

```
Q-CASCADE
verdict: sufficient | escalate-fable
reason: <one line>
```

`escalate-fable` (recovery, after Grok-Q downgrade) fires **only if** (worker binds ≥1, names which):
- detent ≥ `wide` (architecture suitability / rival architectures live), OR
- ≥1 ask-now row is a **meta-fork the L0 could not rank** (two hypotheses left co-primary), OR
- **recurrence class** — the friction is a sibling of a previously path-sim'd + closed arc (completion/handoff class).

On the default Fable-Q path, footer may still emit `Q-CASCADE: sufficient` (already on Fable — no further escalate). `sufficient` after a Grok downgrade is the usual closed/standard outcome with a cleanly ranked ask-now set.

**Cascade shape when recovering from Grok Q (greater sharpens, ¬ re-debate):**

```
[optional downgrade] Grok L0 (draft ranked table) → [escalate-fable] → lead CDP (Fable Max, same pin)
  ⇒ Fable inherits Grok's table as INPUT; revises ask-now / kills rows / tightens ONE Question set
     under a NARROWER declared detent — ¬ re-enumerate from zero, ¬ MAD
```

| Rule | Detail |
|---|---|
| Default | Lead CDP Fable Q — already on; no escalate needed |
| Recovery trigger | Worker `Q-CASCADE: escalate-fable` **after Grok-Q downgrade** (self-decided) — lead honors; lead may also force on explicit operator ask |
| Transport | Fable ⇒ **web-anthropic-cdp** `team_dispatch(model=cdp/fable)` / `project_ask` (Use the `claude-ai-cdp-navigation` skill); ¬ `anthropic/*` API; Opus Max only if Fable unavailable on **this Q hop** (rare Q-CASCADE fallback — ¬ R-admit Opus, which is the usual `cdp/opus-5` event on the coordination thread) |
| Input (recovery) | Grok sidecar = ranked tables (not discarded); Fable output = revised ask-now + killed rows + one tightened Question set |
| Sidecar | `cortex://notes/system/threads/path-sim-{slug}-fable-l0-q.md` (default) or `…-fable-l0-qsharpen.md` (recovery after Grok draft); A consumes the Fable Question set |
| Skip escalate | Default Fable Q, or Grok-downgrade `sufficient` ⇒ A dispatch consumes that Question set |

¬ run Grok+Fable as a parallel A/B by default — that was a one-shot substrate experiment (bus:5496); the production shape is **default Fable Q**, with optional Grok downgrade + serial recovery sharpen.

### Bundled dispatch (two stages — default `/path-sim` on `judgment_required`)

Lead auto-advances legs without operator "go": **recon → lead CDP Fable Q** → Stage-A worker (A+Gate-2) → **lead CDP R-admit** → Stage-B implement → R-after → closeout. Q and R-admit sit **on the lead** (CDP); A/implement are worker dispatches.

**Stage-A worker** — A + Gate-2 densify closeout → **halt at admit-gate; ¬ implement, ¬ R, ¬ Q inside the packet** (Q already completed on CDP Fable). Packet `<task_guidance>` phase 2–2.5; explicit `STOP after Gate-2 closeout — lead runs R`. Template: `tmp/prompts/path-sim-{slug}-dispatch-packet.md`. **Precondition:** Q sidecar present (§ Auto-advance → A).

```
team_dispatch(
  op=generate, seat=cursor-sdk, model=cursor/grok-4.5,
  contract=light-bounded,
  dispatch_thread_id=<bus thread id>,
  packet_path=tmp/prompts/path-sim-{slug}-dispatch-packet.md,
  skills=[path-sim, cheap-recon-before-escalation, cursor-sdk-instruction-standard]
)
```

#### Stage-A Gate-2 densify closeout (mandatory before halt)

Stage-A worker completes Gate-2 **before** halt — R corpus must cite the dense spec as implement SOT. **Projection rule:** dense spec body = authoritative implement cargo projected from the A-bind; A sidecar = ranked L1/L2 tables + rationale + R index — **not** a second implement truth.

**Framed arcs (BINDING — 5966):** when Q recorded `frame_verdict`, Gate-2 densify is conditioned on that verdict — `contradicted` / `sharpened` ⇒ project A-bind against `frame_delta` (operator frame lost or narrowed); **¬** densify from density keyword alone while ignoring a contradicted frame. `adopted` ⇒ densify under the attested Question as usual.

Ordered closeout (worker-owned):

1. `cortex(doc_template, doc_type=implement_dense_spec)` → write/overwrite `cortex://notes/system/specs/{slug}.md`.
2. Set todo `source_uri` to that path via `entity_update`.
3. Fill all 8 sections + `<reasoning_trace>` as **projection of the A-bind** (Use the `handoff-packet-authoring` skill — accepted heading phrases).
4. `cortex(doc_validate, path=…)` until gates 6/8/9 PASS (`authoring_mode`); on fail report missing sections (optional: `implement_ready_preflight(source_ref=todo:{slug})` surfaces `resolution.missing_sections` + `doc_template_hint`).
5. Distill non-empty `files_expected` + `acceptance_criteria` onto todo attrs.
6. Record confirmed `implement_ready` assertion citing dense-spec path + current `spec_sha256:` token.
7. **Halt — explicit STOP; lead runs R-admit.**

**Freeform ban (BINDING):** ¬ densify by inventing a section skeleton. Step 1
`doc_template` is the mandatory start; freeform numbered-section / Bound-forks
notes are **not** Gate-2 closeout. `fs`-readable `source_uri` and dense-spec
**schema** are independent checks (path loads ⇏ schema PASS;
`implement_spec_unreadable` and schema/`implement_spec_not_dense` may co-occur).

**Densify before R (BINDING).** Gate-2 closeout completes **before** lead R-admit — not after. If R verdict ∈ `{ADMIT_WITH_AMENDMENTS, RATIFY_WITH_CONDITIONS}`, lead amends the dense spec to reflect R-bound changes, re-runs `doc_validate`, refreshes `implement_ready` assertion `spec_sha256`, **then** auto-advances to Stage-B — prevents `implement_spec_drifted_since_ready`.

**Lead R (default-on, CDP)** — on Gate-2 closeout OK (A sidecar + dense spec PASS + attrs + implement_ready), the **lead** stages review corpus to `cortex://` and fires `project-ask` per `claude-ai-cdp-navigation` § Path-sim R-admit; writes verdict sidecar `…/path-sim-{slug}-web-anthropic-review.md` citing the **CDP harvest URI**. **¬ delegate R to the worker.** Staging review corpus ≠ RAG activation: when the CDP endpoint is **MCP-enabled for RAG**, the executing agent calls `rag` via MCP (live or `mapped=true`) even when a corpus URI is mapped; lead does **¬** post-hoc merge a staged RAG harvest into the expand (`decision:cdp-rag-via-mcp-not-lead-merge`).

**R prompt life skills (Customize Skills — synced):** nudge body MUST engage by canonical name — `Use the reasoning-posture skill` + `Use the frontier-reasoning-discipline skill` + `Use the consult-posture skill` — then keep the injected R-gate (`¬` path-sim / `¬` L0 reopen / ADMIT enum). Template: `cortex://notes/system/templates/path-sim-cdp-review-nudge.md`. ¬ inject full skill bodies (already on Customize Skills).

**R-admit CDP recipe (BINDING — friction 24967):** default sealed R on **`/new`**, not an endeavor Cowork Project.

**Primary (preferred when `cdp/` model-endpoint is live):** stage prompt to `cortex://…` and dispatch from any vortex-code seat:

```
team_dispatch(
  op=generate,
  model=cdp/opus-5,
  contract=light-bounded,
  sidecar_ref=cortex://notes/system/threads/path-sim-{slug}-r-prompt.md,
  # or prompt=… when short
  dispatch_thread_id=<pending-or-arc-thread>,
)
# poll: agent_bus.wait from poll_hint (from_agent=cdp) — reply OR DELIVERY FAILED
# OF2 resume: CHECKPOINT Next-pickup keeps poll_hint / from=cdp bus-turn anchor
# (not execution_id-only — that shape belongs to the project_ask escape)
```

**Escape — MCP `project_ask` (IF6 / satellite-direct / holder emergency only):**

```
project_ask(
  op=submit,
  prompt_uri=cortex://notes/system/threads/path-sim-{slug}-r-prompt.md,
  converse=true,
  no_project_uuid=true,
  model=opus-5,
  purpose=ask,
)
# client polls: project_ask(op=poll, execution_id=…)
# completion proof: archive_uri OR content_proof (+ consumer sha re-verify)
```

**CLI dogfood / fallback (lead on hub checkout — escape path):**

```
scripts/cortex/claude-ai-sync-jupiter project-ask \
  --register --purpose ask \
  --converse --no-uuid \
  --model opus-5 \
  --prompt-file tmp/reviews/path-sim-{slug}-r-prompt.md \
  --out-dir <mcp-data>/notes/system/threads/path-sim-{slug}-r-harvest
```

| Rule | Detail |
|---|---|
| Default surface | `/new` via cdp/ model-endpoint (or escape `--converse --no-uuid`) |
| Sealed unattended (a:26156) | R prompt MUST include: answer with best judgment; state assumptions; ¬ clarifying questions; ¬ wait for human — see `claude-ai-cdp-navigation` § Sealed / unattended. ¬ charter auto-reply to Cowork Qs |
| Forbidden | Endeavor Project `--uuid` (e.g. SCC `019f6917…` / `cdp_ask_falsifiers.py` `PROJECT`) unless operator/todo **explicitly** binds that project |
| Forbidden | Grepping falsifier harnesses or stale scripts for a "default" UUID |
| Prompt path | Checkout-relative under `tmp/reviews/…` when using CLI escape (`claude-ai-cdp-navigation` § Prompt-file path contract) |
| Optional later | Dedicated review Project UUID in cortex **only if** `/new` lacks required life MCP tools — mint intentionally; ¬ reuse endeavor chrome |
| `archive_path` | **Optional** on MCP submit escape — omit to use execution-scoped default `cdp-ask-archive-new-{execution_id[:8]}.md` |
| Harvest→provenance | `cdp/` harvest→`consult_provenance_from_r_admit` parser path may be **unexercised** until first live `cdp/` R-admit — verify at that dogfood; ¬ claim tested primary for that branch yet |

Evidence: charter-drift R ledger used `project_uuid=""` / `project_url=https://claude.ai/new`; 24951 R polluted endeavor chrome by copying the falsifier UUID.

**Skip R (closed set only):**

| Allowed skip | Evidence required before Stage-B |
|---|---|
| Operator `check_requested=false` / says "no check" | Todo attr or coordination-thread turn quoting the operator |
| CDP genuinely unavailable (Jupiter lane down, cookies stale) | Review sidecar `reason_code=cdp_unavailable` + `friction(owner=agent_skill:path-sim)` |

**Forbidden skip rationalizations (BINDING):** lead claims "mechanical" / "simple bind" / "A already scope-RATIFY'd" / "credits thin" / "I'll self-ADMIT" — **run CDP R anyway**. R's job includes challenging the mechanical claim.

**R-admit poll — long-running ≠ stalled (BINDING — operator 2026-07-21):**

| Signal | Meaning | Lead action |
|---|---|---|
| `completion_phase=running` ∧ `stall_stage=null` | **In flight** — Opus/Cowork often takes many minutes | **Keep polling** via `agent_bus.wait` / `project_ask(op=poll)` (escape); ¬ abort; ¬ invent `cdp_unavailable`; ¬ Stage-B / in-seat implement |
| `turn_idle` alone | Idle page — not advance-eligible | Keep polling toward `content_proof` / `archive_uri` |
| `content_proof` / non-null `archive_uri` | Proof available | Consumer sha re-verify → write R sidecar → auto-advance |
| `failed` + non-null `stall_stage` | Stall lane (Overloaded / timeout / harvest error) | Diagnose per `claude-ai-cdp-navigation`; may re-fire or (only if lane/cookies proven down) `cdp_unavailable` |

`¬ equate wall-clock duration with unavailable.` Presence P4 (don't pin one Shell/Await for multi-minute blocks) means: poll in short slices, end a turn with `execution_id` still live, resume poll next turn — **not** skip R or self-certify. Abort while `status=running` ∧ `stall_stage=null` is a cascade defect (dogfood 2026-07-21 friction 25677 R-admit).

**Window liveness:** poll exposes `streaming`/`stop`/`tool_pause`/`liveness_observed_at` while `status=running` ∧ `completion_phase=running` (a:25681 closed). Prefer those over wall-clock. Nulls while `running` ⇒ harvest not yet sampled — keep polling; ¬ invent stall from duration. Ladder samples come from the **held** ask/converse page (friction 25671 — ¬ competing CDP reconnect). **Lead MAY SSH inspect** via Jupiter CDP lane (`list-lanes` → `harvest_assistant`) when poll is opaque.

**Overloaded banner (operator bind 2026-07-21 / friction 25684):** `Overloaded` is a **delay/rate-limit overlay**, not resumption — the banner can **stay visible after the answer landed**. If window shows `¬in_flight` + substantial `body_len` + lingering Overloaded, treat the turn as **complete** (`chat_reply_wait` structural gate wins; closed 25684) — ¬ infer active generation or stall from the banner alone.

### Auto-advance checklist (binding before each hop)

Lead MUST verify before auto-advancing:

| Before | Require |
|---|---|
| → A | Q sidecar present with verdict (unframed: ranked Q table; framed: `frame_verdict` + `frame_delta` on record) — **¬** advance on `q_skipped` (retired) |
| → R | A sidecar present with ranked L1∧L2 + recommended bind; todo `source_uri` set to `cortex://notes/system/specs/{slug}.md`; dense spec passes `doc_validate` gates 6/8/9; `files_expected` + `acceptance_criteria` non-empty; `implement_ready` assertion cites current `spec_sha256:` |
| → Stage-B implement | R-admit sidecar present with **CDP harvest URI** (`archive_uri` **or** `completion_phase=content_proof` after consumer fs-read + sha re-verify on `content_proof_uri`) + verdict ∈ `{ADMIT, ADMIT_WITH_AMENDMENTS, RATIFY, RATIFY_WITH_CONDITIONS}` **or** allowed skip evidence from the closed set above; **`implement_ready_preflight(source_ref=todo:{slug}).admitted === true`** (safety-net — surfaces gate-9 `missing_sections` early); if R-admit amended bind: dense spec re-validated + assertion `spec_sha256` refreshed. **Halt** if same `source_ref` already has a non-terminal cursor-sdk `contract=implement` (probe `manage(busy_status)` for write-lease/holder awareness — platform ledger reject on duplicate same-ref is authoritative; checklist is co-control, not a substitute). **Forbidden:** advance on `turn_idle` alone or sidecar path without consumer sha re-verify; `delete_after` / cleanup requires archive-proof — never content-proof alone |
| → R-after | Stage-B implement closeout present; dense spec + `files_expected` + `acceptance_criteria` still current; lead fires `/work-item-review todo:{slug}` with **`seat=cursor-sdk, model=cursor/grok-4.5, contract=light-bounded`** (default-on) **or** allowed skip evidence from the closed set |
| → Closeout / todo-close | R-after verdict sidecar present (`RATIFY|REVISE|SCOPE-DRIFT` + cursor-sdk dispatch/harvest URI) **or** allowed skip evidence; REVISE findings applied or follow-up todo seeded; docstring criticals=0; event-instrumentation closeout one-liner when applicable |

`¬ fire Stage-B` when R sidecar is lead-authored prose without CDP harvest (self-certify theater). Lead MAY run `implement_ready_preflight` before R when spec is still stub — early surface only; **not** a substitute for Stage-A Gate-2 closeout.

**Stage-B worker** — after R **ADMIT**/**ADMIT_WITH_AMENDMENTS**, lead fires implement with the R-amended bind:

```
team_dispatch(
  op=generate, seat=cursor-sdk, contract=implement,
  source_ref=todo:{slug}, dispatch_thread_id=<bus thread id>,
  skills=[path-sim, cheap-recon-before-escalation, docstring-quality, event-instrumentation-discipline, cursor-sdk-instruction-standard]
)
```

**Docstring conformance (BINDING — Stage-B + review + lead closeout):**

| Who | Duty |
|---|---|
| Gate-2 / A densify | When bind adds public surface, project docstring conformance into dense-spec `acceptance_criteria`. |
| Stage-B worker | Author conforming docstrings on every **new/changed public** module/class/function at write time (Use the `docstring-quality` skill bar). |
| **R-admit** | Challenge AC presence for public-surface binds — ¬ scan (§ R positions · Docstring in review). |
| **R-after** | Scan `files_expected` — **criticals=0** or return (§ R positions). |
| Lead closeout | `scripts/docstring-quality scan` (or `check` on touched files) — **criticals=0** before todo-close. Cite scan path/exit in implement-closeout sidecar. |

**Composer fitness:** Stage-B Composer is fit for **write-time** docstring authorship to clear **criticals** (empty). It often leaves **too_short / name_echo** on new public surface. When the arc will feed arch-doc / RAG (or closeout / R-after scan shows concentrated warnings on touched public symbols), lead runs **`/docstring-enhance {touched-dir}` (CDP Sonnet)** before todo-close — ¬ Stargate API. R-admit does not replace this pass; R-after can flag it.

**Self-certify = exception, not norm.** Allowed `reason_code` ∈ {`cdp_unavailable`, `operator_no_check`} only. MUST log that code on the review sidecar + open `friction(owner=agent_skill:path-sim)`. On the lead seat CDP is available by default → self-certify there is a defect (2026-07-18: R inside worker packet; **2026-07-17: lead skipped R as "mechanical"**).

### Cascade anti-patterns (Q→A→R)

| Bad | Good |
|---|---|
| Freeform dense note → stamp `implement_ready` / Proceed | `doc_template(implement_dense_spec)` first → fill → `doc_validate` 6/8/9 PASS + attestation → then ready |
| "`source_uri` fs-readable ⇒ dense enough" | Readable path and schema are independent; both required before ready |
| Lead: "mechanical ⇒ skip R" | Fire CDP R; let web-anthropic challenge that claim |
| Lead: "`running` for N minutes ⇒ stalled / `cdp_unavailable` ⇒ abort + implement" | Keep polling until `content_proof`/`archive_uri` or `failed`+`stall_stage`; wall-clock alone ≠ unavailable |
| Lead self-writes `…-web-anthropic-review.md` ADMIT without CDP | Real `project-ask` harvest URI on the R sidecar |
| Stamp `recon_waived` / `skeptic_ratified` as if path-sim R ran | Path-sim R is CDP; skeptic is `role=skeptic` — keep separate |
| R-admit `--uuid` from falsifier / endeavor chrome map | `--converse --no-uuid` on `/new` (§ R-admit CDP recipe) |
| Pre-pinned / operator-framed Question ⇒ skip Q (`q_skipped`) | Framed ⇒ adopt-or-contradict Fable Q (`frame_verdict`); lead-pre-pinned ⇒ thin off-seat Q; then A |
| Dispatch path-sim **A** with `xai/grok-*` | `seat=cursor-sdk, model=cursor/grok-*` (coding lane) |
| Default bundled Q to Grok / skip CDP Fable without closed-detent or operator skip | Default Q = CDP Fable; Grok Q = closed-detent carve-out or explicit skip only |
| Default Q to Opus CDP (same seat as R-admit) | Q = Fable CDP; R-admit = Opus CDP — keep seats distinct |
| Stage-B before R sidecar / allowed-skip evidence | Auto-advance checklist above |
| Stage-B / closeout without docstring-quality (criticals uncleared) | `skills=` includes `docstring-quality`; lead + R-after scan criticals=0; CDP enhance if warnings starve feedstock |
| R-admit skips AC docstring challenge on public-surface bind | Amend/return until dense-spec AC names docstring conformance |
| R-after reviews ship without docstring-quality scan | Scan `files_expected`; criticals=0 or RETURN |
| Skip R-after after path-sim Stage-B (no closed-set evidence) | Fire `/work-item-review` · `cursor/grok-4.5` — delivery half of external R (§ R positions) |
| R-after via web-anthropic CDP / `anthropic/*` as default | R-after substrate = cursor-sdk Grok (checkout-native); R-admit stays web Opus |
| Dispatch R-after with `xai/grok-*` artisan | `seat=cursor-sdk, model=cursor/grok-4.5` (coding lane) |
| R-after silent on event-bearing ON_CHARTER delivery | Challenge closeout one-liner + missed log→event/prune (§ Event instrumentation in review) |
| Path-sim without RAG ⇒ incomplete / block Q | Tier-1 anchors when needed; RAG optional (§ Recon) |

### Todo lifecycle bind (mandatory before dispatch)

**Consult slug** = todo slug. **Bus thread slug** = `path-sim-{slug}`.

| Attribute | Value |
|---|---|
| `workflow_state` | `in_progress` |
| `dispatch_lane` | `path-sim-admit-gate` (todo attr / cursor-sdk LB only — never on `model=cdp/*`) |
| `density_triage` | `judgment_required` |
| `executor_harness` | `cursor-sdk` |
| `required_skills` | `path-sim`, `cheap-recon-before-escalation`, `docstring-quality`, `event-instrumentation-discipline` (+ domain floor) |

**Friction entry (`/path-sim friction {id}`):** `assertion_get` → create/link `todo:{slug}` → attrs above → cite friction on todo → continue. **Optional secondary seed (D7):** after todo create, `cortex(doc_template, doc_type=implement_dense_spec)` + fs-write stub to `cortex://notes/system/specs/{slug}.md` + set `source_uri` — improves stub quality; **not sufficient** without Stage-A Gate-2 closeout (fill → validate → distill → implement_ready → STOP).
