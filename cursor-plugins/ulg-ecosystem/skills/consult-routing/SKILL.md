---
name: consult-routing
description: "On dispatch-routing — team_dispatch op/role/contract, cursor-sdk lane=, code vs non-code lane, Gate-2 densify, autonomous work-item spine, or implement_ready gates."
---

# Consult Routing

**SOT:** dispatch-target routing and densify/implement lanes.

**Posture:** consult token ⇒ Use `consult-posture` before transport.

**RAG:** default live `rag(op=search)`. MCP-enabled endpoint: executing agent owns `rag`; lead ¬ merge staged RAG
(`decision:cdp-rag-via-mcp-not-lead-merge`; CDP: `claude-ai-cdp-navigation` § RAG ownership).

## Surface gate (life vs code)

Life MCP excludes **CODE_EXTRA** — every primary on `/mcp/code` absent from `/mcp/life`.
**Do not hand-maintain the list.** Derivation (same as the boot card and
`endpoint_surface.derive_code_extra_primary_tools`):

`derive_surface_primary_tools("code") − derive_surface_primary_tools("life")`

Current registry wiring (illustrative — re-derive before citing): `manage`,
`observability`, `panel_dispatch`, `pipeline`, `team_dispatch`.

Cognitive routing on every seat; CODE_EXTRA = **code MCP only**. On life: in-seat
cognitive legs, `agent_bus` code-seat transport, or honest deferral. Capability
gap: `life-to-code-request-lane` (`lane:life-to-code`).

### CDP transport (MCP `project_ask` removed)

Product = `team_dispatch(model=cdp/opus-5|cdp/fable|cdp/sonnet-5)` → poll `poll_hint`.
Delivery complete iff `chat_url` observed or followup `send_verified` — Stargate admit ≠ on claude.ai (`claude-ai-cdp-navigation` § Dispatch delivery).
Warm paste / attended resolve = `cse_session(op=followup|resolve_attended)`.
Admission/busy = `manage(action=busy_status)`. IF6 / satellite-direct submit =
CLI (`scripts/cortex/claude-ai-sync-jupiter project-ask`). MCP `project_ask`
is gone from `tools/list` — do not call it.

**Purpose-keyed skill floor** (`ensure_cdp_judgment_skills`): every purpose
merges `ulg-for-llms` + `reasoning-posture`. `ask` also prepends the arch pair
and adds `hypothesize-simulate`; `review` adds `consult-posture` and
`hypothesize-simulate`; `mission` / `operator-proxy` add
`cdp-operator-proxy` and `hypothesize-simulate`. Caller `skills=` is additive.
Omitted purpose + `cdp/sonnet-5` → `produce`; omitted + opus/fable → `ask`.
Stock container skills (`docx`/`xlsx`/`pptx`/`pdf`/`skill-creator`/…) are a
**prompt verb**, never `skills=` (`cdp_skills_unknown` 422 is the collision
guard). Consults about claude.ai / Cowork / the picker itself prime
`product-self-knowledge` the same way (prompt, not `skills=`).

| Job | Picker | Effort | Transport | `purpose` |
|---|---|---|---|---|
| G1 / Mode B / path-sim Q / hop-5 check | `cdp/fable` | high (max when bind gates a wave) | fresh `/new`; followup only into a live op-proxy CSE | `ask` |
| G2 frame | `cdp/fable` followup in the G1 CSE; else `cdp/opus-5` fresh | high | followup ≻ fresh | inherit `ask` |
| BIND (score-play M1) | `cdp/fable` if architecture-open / ≥2 rivals / invariant-touching; else `cdp/opus-5` | high (Fable max when bind gates a wave) | fresh; 0 turns when zero forks ∧ G1 edge resolves ∧ mechanical | `ask` |
| SKEPTIC@BIND (score-play M2) | `cdp/fable` high when Opus bound; **`cdp/opus-5` xhigh/max when Fable bound** | see picker | 2nd CDP, identity ≠ binder; `panel_dispatch` when M2 known pre-dispatch | `ask` or `review` |
| GATED REVIEW pre-go-live (score-play M3) | `cdp/opus-5` | xhigh — pin `reasoning_effort="high"` minimum; xhigh on critical path | pre-LAND gate; fires on M3 predicates only. Transport fail ≡ stop past gate (`conductor` a:32226) | `review` |
| R-admit / verifier / mission / M-Arch | `cdp/opus-5` | high | fresh (R); mission followup | `review` or `mission` |
| G6 pre-land arc review (score-play M4) | `cdp/opus-5` | high — pin `reasoning_effort="high"` (do not inherit conductor admit `effort:max`) | **default-on at G6 — after G5 implement, before any land or DONE claim** — **review harvest ≺ land ≺ DONE** (`conductor` stronger-model gates · a:32146). CDP stall / empty FAILED / `cdp-ask` down ≡ no harvest ≡ **HARD STOP** — ¬ DEFERRED-and-proceed; ¬ silent Cursor substitute. Latency-only while CDP healthy: poll / hop+watcher / `PARKED_TRANSPORT` until harvest. Stage closeout + `files_expected` to `cortex://`; ¬ diff artifact. Skip only under full-skip (runbook:score-play § Explicit skip). ¬ a silent G4 | `review` |
| Docs / closeouts / spec polish / office I/O / dashboards | `cdp/sonnet-5` | **Extra** default; **Max** via `reasoning_effort=max` | pipeline or fresh; office → `harvest_source=output-file` | `produce` |
| Skill authoring | `cdp/opus-5` draft · `cdp/sonnet-5` revise | high / Extra | fresh, output-file → cortex staging | `produce` |
| Haiku | `cdp/haiku-4.5` | — | **no recipe** until Sonnet caps | — |

Standing: verifier ≠ producer; independent check ≠ author; Other Models /
`cursor/claude-fable-5{,-1}` are not substitutes. Slash commands cite this table —
they are not SOT.

### Fable 5.1 SDK outage (a:32393 — open until recovery probe passes)

**Verdict (a:32403):** upstream Cursor serving defect on `claude-fable-5-1` over the
**SDK/agent surface**, knob-independent — hollow ~5s / 0 tools at `medium`, `xhigh`,
and `max` after 2026-09-05 ~14:33Z. Wire and detection gaps are closed; do **not**
denylist `xhigh` on the card.

| Need | Route while outage open | Do not |
|---|---|---|
| Judgment / width | `cdp/fable` (Cowork transport) | Rebind to another Fable SDK rung |
| Bind / sketch on SDK | `cursor/claude-opus-5` `{high\|xhigh\|max}` | `cursor/claude-fable-5{,-1}` on cursor-sdk |
| Mechanical | `cursor/composer-2.5` | Third retry at a different Fable effort |

**Recovery probe:** one trivial Fable SDK run — body non-empty and duration >15s ⇒
stand down this row. Empty ~5s ⇒ still broken; route off model, not knob.

SoT: `cortex://notes/system/specs/a32393-narrow-fix-bind.md` (verdict, routing, upstream report template)

## Dispatch targets (code surface only)

Poll `poll_hint` with `agent_bus(wait)`, not `pipeline(result)`.

| Want | Use | Prompt rule |
|---|---|---|
| Model answer, auto thread | `team_dispatch(op="generate", role=…, contract=…, prompt\|sidecar_ref)` | Atomic prompt; ¬ “reply on this thread” unless handoff. |
| Existing thread | `op="to_thread", thread=…, prompt=…` | Stargate writes turn. |
| Manual seat | `op="handoff", role/seat, packet_path\|source_ref` | “Reply on this thread” OK here. |

Use admit `reply_from_agent` for `wait(from_agent=…)` — ¬ infer from `resolved_model`.

## Judgment skill (BINDING)

**Attended IDE:** `reasoning-posture_ulg.mdc` is `alwaysApply` + `required_gate` on
thinking models — read the skill body on substantive turns.

**Headless** (`team_dispatch` / `cursor-sdk` / `cursor-auto`): alwaysApply rule
pruned from the cursor-sdk dispatch HOME; judgment contracts get preamble injection
only (mechanical/quick skip). `skills=` on cursor-sdk generate mounts as well —
staged into the dispatch HOME for native discovery, Use-line deduped against the
fixed preambles, so listing `reasoning-posture` there adds nothing on a contract
that already injects it.

| Path | How |
|---|---|
| `op=generate` `seat=cursor-sdk` | GIW `resolve_prompt_preamble` on judgment `handoff_contract`; skip mechanical/quick. Caller `skills=` staged into HOME `.cursor/skills/` + Use-line (`cursor_sdk_skills_mount`) |
| `cursor-auto` admit | Admit report appends `REASONING_POSTURE_PREAMBLE` when `handoff_contract` warrants |
| `op=handoff` consult / light-bounded | Enrich Block 2 `Use the reasoning-posture skill`; skip implement / `cursor-implement` |
| CDP `model=cdp/…` generate | `skills=` merge (`ensure_cdp_judgment_skills`, purpose-keyed — § CDP transport) |

Packet MAY still open with the invoke as belt-and-suspenders. SOT: skill `reasoning-posture` § Always-on injection.

## Code vs non-code (`dispatch_lane`)

SoT: `config/routing/route_policy.yaml`. Substrate-derived — ¬ role proliferation.

| Lane | Signal | Default | Work |
|---|---|---|---|
| **code** | `cursor-sdk` | `generate seat=cursor-sdk` | recon, implement, light-bounded |
| **non-code** | API roles | `generate role=…` or handoff | adversarial, life-domain, analysis |

Settled implement/recon/review → `cursor-sdk` (R2). **Model split:** `implement` → Composer;
IDE/Task breadth recon → **Explore subagent** (`Task(subagent_type="explore")`; ¬ tool);
recon+investigate judgment residual → Composer `contract=investigate` (facts + `OPEN FORK:` — never binds);
pure inventory / Task-unavailable fallback → Composer. `cursor/*` only on `cursor-sdk` → else `422`.

## Bind-then-compose split (judgment closed → nested Composer)

**Invariant:** `judgment_closed ∧ mechanical_remainder ⇒ split_dispatch` — premium / reasoning
models bind; **`cursor/composer-2.5`** implements nested (`seat=cursor-sdk`, omit `model=`).
Rule stub: `dispatch-kernel_ulg.mdc` § Hard walls.

| Leg | Model / seat | Contract | Delivers |
|---|---|---|---|
| Bind | `cdp/opus-5`, `cdp/fable`, `cursor/claude-opus-5` (bind scope only) | `light-bounded` | Dense packet / spec: `files_expected`, `acceptance_criteria`, invariants — ¬ repo implement |
| Compose | `seat=cursor-sdk` (Composer default) | `implement` \| `pure-mechanical` | Mechanical edits + verify |

```python
team_dispatch(
    op="generate",
    seat="cursor-sdk",
    contract="implement",
    packet_path="tmp/reviews/{slug}-implement.md",  # or source_ref=todo:{slug}
    dispatch_thread_id="{arc-id}",
    nest_under="{parent_dispatch_id}",  # required when parent holds cursor_sdk_gate
)
```

**Signals to split:** `sdk_cost_risk` at admit · implement-shaped packet on premium model ·
`executor_override` naming Opus for mechanical work · `density_triage=judgment_required` with
write step remaining.

**Exempt:** bind-only consult (no writes) · trivial in-seat touch · dense packet already
authored — compose leg only (`lean-context-dispatch-first` non-primary gate).

**Anti-patterns:**

| Bad | Good |
|---|---|
| `cursor/claude-opus-5` + `light-bounded` + implement acceptance in one packet | Opus bind sidecar → nested `cursor-sdk` `contract=implement` |
| `executor_override: cursor/claude-opus-5` on mechanical implement | Omit `model=` on `seat=cursor-sdk` implement (Composer default) |
| Ignore `sdk_cost_risk` at admit | Split or downgrade to Composer before edits |
| Premium model runs quality_gate/pytest loops on known files | Composer leg + lead verify sample |
| Treat the split as guidance the orchestrator may skip when in a hurry | On `cursor-auto` it is substrate; the redirect fires whether or not you meant it |

## cursor-sdk model name surfaces

| Surface | Form | SoT |
|---|---|---|
| cursor-sdk `team_dispatch` | `cursor/{bare}` + knobs | `CURSOR_MODEL_CAPABILITIES` |
| Code-lane check/review default | `workflows.check_review.model` | `config/routing/route_policy.yaml` |
| Task subagent | kebab slug | Task roster |
| Cloud API | `provider/model` | `capability_dispatch` |

¬ Task slugs or catalog IDs on `seat=cursor-sdk`. Non-workflow-primary `cursor/*` ⇒ **OPERATOR-GATED**
(`decision:cursor-non-primary-model-operator-gate`).

<!-- workflow-registry:v1:start -->
### Workflow registry (generated from config/routing/route_policy.yaml)

- **policy_version:** `2026-09-02`

**Stargate omit-model default:** `workflows.auto_judgment.model` (same SOT as
GIW Auto lane `resolve_desired_model(auto)` for judgment contracts).

| workflow | seat | model | contracts |
|---|---|---|---|
| auto_judgment | cursor-sdk | cursor/composer-2.5 | answer, confer, ask, verify, execute, propagate, light-bounded |
| check_review | cursor-sdk | cursor/gpt-5.6-terra | — |
| investigate | cursor-sdk | cursor/composer-2.5 | investigate, recon, seed |
| mechanical_implement | cursor-sdk | cursor/composer-2.5 | implement |

**Roaming bare ids:** `composer-2.5`, `composer-2.5-fast`, `grok-4.6`

**contract_effort** (omit/auto defaults; contract-keyed, not per-workflow):

| contract | effort |
|---|---|
| answer | medium |
| ask | medium |
| confer | xhigh |
| execute | xhigh |
| implement | medium |
| investigate | xhigh |
| propagate | xhigh |
| recon | medium |
| seed | xhigh |
| verify | xhigh |
<!-- workflow-registry:v1:end -->

## Non-primary model gate — discriminator

| Term | Meaning |
|---|---|
| **Workflow-primary** | A model a standing rule/skill already names as the autonomous default for that need (dispatch-kernel ladder · this skill · `path-sim` · `subagent-strategy`). Omitting `model=` when the harness inherits the session/role default counts as primary. |
| **Non-primary** | Any other explicit bind — e.g. `gpt-5.6-sol-*`, off-table Task slugs, or a ladder model used for the **wrong** work class (Sol/Opus for mechanical work after judgment closed). |

**Named exceptions** (fire without re-asking; still announce): path-sim **A** → Composer enumerate + **`cdp/fable` bind** · path-sim **Q** → `cdp/fable` · implement → `cursor/composer-2.5` · CDP trigger → `cdp/opus-5` · escalation-warranted `cursor/claude-opus-5` under the premium inform-then-proceed row.

**Anti-pattern:** re-spend frontier reasoning (Sol / Opus / Fable) to *implement* amendments a prior consult already densified — that is non-primary for the mechanical class.

## Judgment escalation ladder — anti-patterns

Ladder + binder order live on `dispatch-kernel_ulg.mdc`. This table is the operational
guidance that table has no room for — the two are read together.

| Bad | Good |
|---|---|
| Ask operator which lane to enter when spec + scoreboard already bind | Enter the lane; report shape on CHECKPOINT |
| Wait for Opus to ratify a shape cursor already built and verified | Inform Opus executed; continue on tick |
| Park on the operator for `manage` / `charter_reload` / git-tracked implement | Seat executes or implements autonomous recovery; `charter_reload` = loop bounce only |
| Treat every consult as operator-gated | CDP consult is **autonomous** under the dispatch-kernel CDP trigger |
| Skip CDP and go straight to human on judgment forks | Cursor (incl. cursor/opus) → CDP/Fable first; human only when CDP/Fable flags `ESCALATE` or operator-only |
| CDP Opus/Fable stuck → Ask the human | `cursor-auto` → nested `cursor-sdk` (`cursor/claude-opus-5` or explicit Other Models pin) (`cdp-operator-proxy` 2b); Terra only if named |
| Treat `cursor/claude-opus-5` as ladder-top — ask human when Opus is unsure | Opus-in-cursor is step 1; consult an independent binder (step 2) before human |
| `cdp/opus-5` ratifies its own output at the same tier | Escalate that artifact to **Fable** (2b) — weight-class independence |
| Treat any two Anthropic seats as self-review and skip straight to GPT | Opus→**Fable** is a genuine check; 2b precedes explicit Other Models (2c) |
| Silent Terra / Other Models as the next binder | 2c is **explicit pin only**; default after Fable is **`cursor/claude-opus-5`** on cursor-sdk |
| Pin `cursor/claude-fable-5` or `cursor/claude-fable-5-1` because Fable is wanted | Blocked for cost (both — 5.1 launched 2026-09-01 at same $/M) — use `cdp/fable`. While a:32393 SDK outage is open, Fable 5.1 on cursor-sdk is also **observed hollow at every tested rung** — use § Fable 5.1 SDK outage |
| Rebind Sketch to Fable max/high on cursor-sdk after xhigh hollow | Falsified (a:32403): `medium` and `max` hollow too — rebind **off the Fable SDK surface** |
| `team_dispatch(model=gpt-5.6-terra)` bare slug on code-lane bind | `seat=cursor-sdk` + `model=cursor/gpt-5.6-terra` — explicit pin only |
| Spend `cursor/gpt-5.6-sol` on broad open-ended review | `sol` is targeted, low-token, still Other Models — explicit pin only |

Rationale for the independence axis (weight class vs family, self-review definition): `decision:gate-independence-not-human-ness`.

## Anthropic-family substrate

| Path | Default |
|---|---|
| `anthropic/*` on `team_dispatch` | **PROHIBITED** |
| `cursor/*` on `cursor-sdk` | OK except **Fable** |
| Anthropic consult / binder / R-admit | **`team_dispatch(model=cdp/opus-5\|cdp/fable)`** + `cortex://` staging — poll `poll_hint` |
| IF6 / satellite-direct submit | **CLI** — `scripts/cortex/claude-ai-sync-jupiter project-ask`. Operator-proxy: `team_dispatch(model=cdp/…, purpose=operator-proxy)`. Warm paste: `cse_session(op=followup)` |
| Live checkout | `cursor/claude-opus-*` |

Rule: `anthropic-dispatch-authorization_ws.mdc`. Fable = CDP only (`cdp/fable` / picker — ¬ `cursor/*` Fable).

## xAI coding-substrate

| Path | Default |
|---|---|
| Path-sim **A** (L1+L2) / closed-detent light consult | Composer enumerate → **`cdp/fable` bind** |
| Path-sim bundled **Q** (L0) | **CDP Fable** — `team_dispatch(model=cdp/fable)` (CLI `fable-5.1` = IF6 only; path-sim annex A) |
| Recon+investigate judgment residual | **`seat=cursor-sdk` + `contract=investigate`** (facts + `OPEN FORK:` — never binds) |
| API `xai/grok-4.6` on coding work | **PROHIBITED** |
| Engineering skeptic on **codework** | **DORMANT** — `grok-4.6` barred on codework (operator ratified agent-bus:9956). Re-evaluate when a successor model (e.g. grok-5) earns admission. Use CDP judgment slots (M1–M4, `runbook:score-play`) instead. |
| Non-code adversarial (life/analysis) | `role=skeptic` + `xai/grok-4.6` — life/analysis lane only |
| Writing / correspondence | Grok **PROHIBITED** — L3 annex |

### Skeptic / reviewer substrate matrix (codework, R2)

Judgment slots are **CDP-default**; mechanical code-lane check keeps Terra on cursor-sdk. Composer is never a checker.

| Slot | CDP (opus/fable) | cursor-sdk Terra | cursor-sdk Composer | panel_dispatch | API skeptic (Grok) |
|---|---|---|---|---|---|
| SKEPTIC@BIND (judgment on spec/forks) | eligible, default | explicit pin only (G4-class family check) | **never** — producer-class, barred from ranking | eligible when M2 known pre-dispatch | **dormant on codework** |
| GATED REVIEW pre-go-live (M3) | eligible, default | explicit pin only | never | — | no |
| Code-lane diff check (`check_review`) | not this slot | **default** (`cursor/gpt-5.6-terra` per `workflows.check_review.model`) | never | — | no |
| S1 background arc review (M4) | eligible, default | explicit pin | never | — | no |
| Non-code adversarial (life/analysis) | eligible | no | no | eligible | per non-code row above |

SoT for M1–M4 predicates and skip conditions: `runbook:score-play` (agent-bus:9956 R2 ratification).

## Implement lane — default source_ref

```python
team_dispatch(op="generate", seat="cursor-sdk", contract="implement", source_ref="todo:{slug}", dispatch_thread_id="{arc-id}")
```

Materializer reads attrs only; spec prose = hash input. Preflight: `entity_get`; `workflow_state ∈ {open,in_progress}`.
`wrap` = materialize-only. Contract↔source matrix: L3 annex.

## Conductor spawn — light-bounded + source_ref

Standing first-utterance (`agent_skill:conductor`):

```python
team_dispatch(
    op="generate",
    seat="cursor-sdk",
    contract="light-bounded",
    lane="B",
    source_ref="todo:{slug}",
    packet_kind="conductor",
    dispatch_thread_id="{root}",
)
```

Kickoff body = Stargate materializer or explicit `packet_path` on non-conductor admits —
**never** `sidecar_ref` beside `source_ref` (`multiple_prompt_sources`). Conductor
forbids `packet_path`; materializer owns the six-block packet.

## cursor-sdk checkout lane (`lane=`)

`team_dispatch(op=generate|to_thread, seat=cursor-sdk)`: `lane=` is a **wire
parameter**, not packet prose. Distinct from `dispatch_lane` (path-sim).

**Caller recipe** — top-level generate **passes** `lane ∈ {A,B}`. Omit is **not**
a preference. MCP + Stargate return 422 `lane_required` on top-level omit.
The only documented omit is inherit:

| Situation | Pass | Why |
|---|---|---|
| implement / in-repo `files_expected` | `lane="B"` | regime default |
| bind-only, empty `files_expected`, cortex-only writes | `lane="A"` | named; ¬ mint a tree |
| out-of-repo / `CURSOR_LANE_B_SCOPE_REFUSED` | `lane="A"` + fix or name the scope | ¬ omit to “get past” (7286) |
| `nest_under` / `resume_of` | omit | inherit parent isolation |

**cursor-auto nested implement-class:** Auto stamps `lane="B"` on nested
cursor-sdk POST for `job.contract` in `{implement, verify}` when `job.lane` is
unset and the leg is not `read_only`. Bind-only / confer / investigate and
other non-implement contracts stay on Lane A (omit or explicit `lane="A"`).
Opus `agent_bus.request(lane=)` remains an optional override — the default must
not require the knob.

**GIW `select_lane` priority** (inference, ¬ a license to omit): explicit A/B ≻
empty `files_expected` → A (`opt_out`) ≻ `contract_regime` B. Empty scope + omit
→ **A** even when regime is on. Do not read “regime on → B” as the omit outcome.

**Preflight:** `manage(busy_status)` — read the **lease holder** /
`active_by_lane` for the lane you will pass. Service-up ≠ slot-free. After
admit, quote `sdk.lane.selected` or `active_by_lane` before naming the lane.

## cursor-sdk satellite git (`workspace=`)

`team_dispatch(op=generate|to_thread, seat=cursor-sdk)`: optional `workspace=`
names an **allowlisted satellite** under `/mnt/torus/projects/{name}` for
per-dispatch **git identity** — capture, Gate D, land lease, `head_sha`,
`git_refs`, Lane-B mint source. **Omit** for hub ULG (default). **One name**
per dispatch; hub name is invalid (422 `CURSOR_WORKSPACE_HUB_USE_OMIT` — omit
instead). Control-plane stays on hub: packet_path read, SDK HOME/plugin,
closeout receipt sidecars under `workspaces://universal-llm-gateway/…`.
Allowlist SoT: `cursor-plugins/ulg-ecosystem/SATELLITES.txt`.

| Situation | Pass | Git identity |
|---|---|---|
| Hub ULG implement | omit `workspace=` | hub |
| Satellite bot (e.g. claudeburst) | `workspace="claudeburst"` + `lane="B"` typical | that repo |
| Sibling write without `workspace=` | omit | hub (sibling = outside_repo — honest) |

`workspace=` does **not** force `lane=B` — caller chooses A or B per checkout
recipe above. Satellite Lane A: cwd = satellite root + lease keyed to satellite.

Stay on one designated tree per arc: reuse when `nest_under`, `resume_of`, or
`lookup_lane_worktree(thread_id)` already holds a worktree — see `git-posture`
§ Stay on one designated tree. `conductor` defers this recipe here.

## Scripts / satellites — composed client surface

Scripts and satellite netns that need a **blocking reasoned hop** POST Stargate
`:9999` `/api/v1/team/dispatch` (what charter-runner `dispatch_client` and
`opus-summons-watchdog` already do). GIW `:8091` is **peer-only** (`nested_sdk`
inside GIW). Never `POST` GIW from a script; never `import cursor_sdk` outside
GIW. Netns: `GATEWAY_URL` as IP — refuse hostname `io`.

Mechanical tools (ESS harvest, Graph, hydra observe, `scripts.local` helpers)
are **not** dispatch clients. Happy path stays SHA-gate / JSON. Failure ⇒ file
a cortex `friction` (owner-typed, categorised, `evidence_uris` → the tool's own
artifact). Local JSON is `evidence_uris` only. The follow-on `todo:friction-…`
is the pickup; any already-admitted actor may fire team-dispatch from it
(conductor, cursor-auto triage, IDE lead, operator via friction-review) — never
the producer, never a new daemon. Netns producers are filed by their host-side
sweep (hydra: the circle). That keep-and-add-kill is **advisory** until
operator ratify
(`document:satellite-script-dispatch-surface-architecture-consult`).

**Happy-path composition (advisory until operator ratify).** Mechanical work is
three layers. Satellites serve HTTP resources (`[universal:satellite]`). **Pipelines
are the composer for any compound, inference-bearing, or replayable step** —
`pipeline(op="run"|"async")` / `POST :9999/api/v1/pipelines/dispatch` against hub
`pipelines/{domain}/v1/` or personal `pipelines.local/` — and are addressed as
one more `:9999` resource. **Scripts, ticks, and circles are thin clients**:
trigger + reach + SHA/JSON gate + friction on failure (O2′). A script never
re-implements an LLM stage, an adjudication loop, or a multi-model merge; when
it grows one, that step is a pipeline missing — author it, shrink the script.
Pipelines own no trigger and reach no satellite today (no HTTP step; `shell_v1`
is network-none); do not push reach into them. Judgment hops remain O1. Cite:
`cortex://notes/system/threads/pipeline-happy-path-composer-fable-answer.md`.

Local surface = the two `:9999` doors (team-dispatch, pipelines) plus this
producer table. Not GIW. Not `import cursor_sdk`. Not a fourth git identity.

| Producer | Happy path (L3) | Compound inference (L2) | Failure (O2′) | Judgment / self-correct (O1) |
|---|---|---|---|---|
| Fleet script / tick | SHA/JSON or thin HTTP | `pipeline` / `:9999/pipelines/dispatch` | `friction` over cortex UDS | POST `:9999/team/dispatch`; hub omit `workspace=` |
| claudeburst / hydra netns | observe + SHA; Opus stub stays; `FORBIDDEN_BINDERS` | **not** from inside netns (no MCP; no pipeline trigger) | **host-side circle** files friction; evidence_uris → container artifact | Host or netns O1 via `GATEWAY_URL` IP (proven). Circle binds auto-live, not Composer |
| `scripts.local` | Playwright / Graph / SHA-gate | `pipelines.local/` YAML (not personal Python into a new identity) | friction; `git_identity=personal` | hub Lane A or artifact-mediated; **never** `SATELLITES.txt` |

Git identity is three distinct: hub (omit `workspace=`), satellite (`workspace=`
from `SATELLITES.txt`, Lane B typical), `scripts.local` (never allowlisted;
reasoned edits Lane A or artifact-mediated). Lane B cannot see gitignored
personal code.

**Facilitation (advisory until operator ratify).** A satellite workflow is
effective when an L3 client can reach typed L1 HTTP and file O2′ on failure;
every-tick predicates live in the satellite loop and are read, not re-derived;
named fires are L1 POSTs with the gate on the endpoint. Pipelines compose the
inference-bearing step only. Do not wait for pipeline satellite-reach (Alt-X,
DEFER) to ship the next satellite job. Missing L1 is a satellite spec, not a
ULG composer gap. The satellite's served OpenAPI is the catalog; this skill
names no paths.

Cite: `cortex://notes/system/threads/satellite-script-dispatch-surface-g2-stamp-vs-migrate-answer.md`
(`read_sha256=79f2bc92620f98ca1c3f1a1a4844cf1acdc7da791db11e43cd76ccd62ccc4c7f`).
O2 envelope draft (superseded by G2, not convention):
`cortex://notes/system/threads/satellite-script-dispatch-surface-o2-envelope-draft.md`.

## Abstraction layering (codework lane)

SOT: `abstraction-layering` skill · `/layer`. Route by highest open layer; mechanical-only skips to
implement. **G1 skip:** active structural `derived_from` from the work item → `document:` with
`consult_kind=architecture` (skill § G1 skip / Stage 0 attach) — ¬ chat inform alone.
Non-codework ⇒ `path-sim`. ¬ R-admit/R-after on codework. Cost-bind table: L3 annex.
(G3 stage token `densify` / Gate-2 densify close — unchanged; not the lane brand.)

## Densify lane — Gate-2 close

Requires `validate_dense_spec` + distilled `files_expected`, `acceptance_criteria`, `required_skills`. Reasoning tier
authors implement-ready at Gate-2; cite `spec_sha256:<hex>`. `density_triage` ∈ {`judgment_required`,`mechanical`};
`source_uri` → spec; eight sections + `<reasoning_trace>` + zero `OPEN:`.

## Autonomous work-item spine

Default `judgment_required` code lane (`decision:autonomous-work-item-spine`):

`recon → settle → densify → check_review (Terra, mechanical) → Composer`

One merged mechanical check via `workflows.check_review` (`cursor/gpt-5.6-terra`). CDP judgment
(M1–M4, batched S1) per `runbook:score-play` (R2, agent-bus:9956). Gate-6 substrate, zoom-out,
seeding ladder, overhaul, `authority_fork`: L3 annex § spine extensions.

## Implement admission gates

`libs/implement_admission/implement_ready.py` · cross-ref `todo-lifecycle` Gates 4–8.

| Triage | Admit |
|---|---|
| `mechanical` | immediate |
| `judgment_required` | full stack below |
| `recon_pending` | blocked |
| other | `implement_triage_unknown` |

**judgment_required:** `implement_ready` + `spec_sha256`; populated `files_expected` + `acceptance_criteria`.
**skeptic:** `skeptic_ratified` cites bus turn + `spec_sha256` + `FILE_EVIDENCE_PATHS:` in turn body.
**recon_waived:** JSON waiver with matching hash. **gate6_ratification_uri:** designated turn — `implement-todo` §3b.

## Address — bind_status chooser (SOT)

Codework default is S4a → spawn conductor (`decision:operator-request-front-door` Q2 ·
unify §3.1). `/layer` is gate-shape, ¬ a second admit. Path-sim is **not** the
unmatched default. `/address` is the settled-ship peer.

Attrs: `bind_status∈{unsettled,settled,shipping,deferred}`, `density_triage`,
`arc_lane`. **PATH-SIM** only when **any**: non-codework · `arc_lane=path_sim` ·
operator named `/path-sim`.

| # | Condition | Route |
|---|---|---|
| 1 | `deferred` | held |
| 2 | `settled\|shipping` ∧ ¬`recon_pending` | **ADDRESS** |
| 3 | no closable `todo:` ∧ codework | **SEED** (`work-item-seed-path` S4a → spawn) |
| 4 | `unsettled` ∧ `judgment_required\|recon_pending` ∧ PATH-SIM trigger | **PATH-SIM** |
| 5 | `unsettled` ∧ `judgment_required\|recon_pending` ∧ codework | **LAYER** (re-admit conductor; `/layer` = gate-shape) |
| 6 | `mechanical` ∨ (`implement_ready` ∧ stamped) | **DISPATCH** |
| 7 | else | **LAYER** (conductor) if codework else **PATH-SIM** |

Gate-2 sets `bind_status=settled`. Mirrors cite `SOT: consult-routing § Address`.
Rows 3 and 5 **are** lid-close / spawn-or-re-admit.

## Codified bug reports

`friction()` = observation. Phase 1 investigate (autonomous spine default) → Phase 2 execute (`source_ref` implement).
Zoom-out (C2): touch-points + sibling grep + `## Secondary findings`. Template: `recon-investigate-packet.md`.
Playbook: `friction-review`.

## Minimal operating summary

API generate/to_thread; handoff for manual; cursor-sdk unattended. Spine default; web mid-pipeline densify/check/implement
forbid. Implement needs live entity + Gate-2 + implement-ready. Verify writes by hash; TERMINAL before cleanup.
Spec ≠ packet. Life corpus → `life-handoff-corpus`. `authority_fork` → escalate.

## L3 detail

`routing-detail-annex.md` — contract matrix, writing substrate, CONFORM/CONVERSE, executor tier, general execution,
write channels, source shapes, task-class model reference.
