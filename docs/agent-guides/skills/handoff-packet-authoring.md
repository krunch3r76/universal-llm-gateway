# Handoff Packet Authoring

Durable checklist for **stage → densify → wrap → dispatch-by-`source_ref`**. Gate-2 consult briefs, dense specs, and server-materialized implement packets share a six-block shape. Default bound implement transport:

```text
team_dispatch(op=generate, role=cursor-sdk, contract=implement, source_ref=todo:{slug})
```

Server materializes from todo attributes; Composer runs automatically; no IDE pickup. Six-block authority: project `.cursor/rules/architecture-handoff-protocol.mdc` § The Six Required Blocks.

**Different artifact:** this governs heavyweight machine-dispatch packets (6 XML blocks, `team_dispatch`). Human-pasteable fresh-session kickoff/pickup prompts use `agent-skills/handoff-prompt-authoring.md` (7-part template).

## Spec vs packet

Spec = durable design/cargo. Packet = ephemeral transport/container for one handoff leg. Re-wrap the same spec for each leg.

| | Spec | Packet |
|---|---|---|
| Nature | durable design — what to build | one handoff payload |
| Path | `tasks/specs/{slug}.md` | `tmp/reviews/{slug}-*.md` |
| Lifetime | persists | dispatch-scoped |
| Author | reasoning tier hardens | dispatching seat wraps |
| Audience | future agents | one receiver |
| Holds | problem/scope/touch points/steps/ACs/`<reasoning_trace>`/forks | six XML blocks |

Never say bare “packet”; qualify:
- **consult brief** — Gate-2 six-block packet, front-matter `contract: consult`, `<output_format>` asks for dense spec.
- **dense spec** — durable design at `tasks/specs/{slug}.md`, fingerprinted by `content_hash`; not instruction source.
- **materialized implement packet** — Gate-3 six-block transport, `contract: implement`, acceptance criteria in `<task_guidance>`, asks executor to edit. Default: server materializes from `todo:{slug}` attrs (`files_expected`, `acceptance_criteria`, `required_skills`).

Lifecycle:

```text
consult packet → spec → implement packet
Gate 2 in        Gate 2 out  Gate 3 in
```

Dense spec ≠ dispatchable packet. Never dispatch a spec raw; re-wrap into implement packet (spec → `<corpus>`, ACs → `<task_guidance>`). Web saying “a spec is not a packet” is this rule firing, not a finding that the upstream consult brief was malformed.

## Dispatch lifecycle

Invariant: reasoning tier (`web-consult` / `cursor-consult` / Opus) authors dispatch-ready specs; mechanical tier (`cursor-sdk`/Composer default, `cursor-implement` fallback) executes. Never reverse. Composer is mechanical; implement packet MUST pin files/functions/tests/forks. Under-specified cursor-sdk packet = routing error.

Read `todo:{slug}.attributes.dispatch_lane` before writing.

| `dispatch_lane` | Who authors | Packet type | Typical seat |
|---|---|---|---|
| `web-implement-packet` | web-claude | consult brief that authors materialized implement packet | `team_dispatch(op=handoff, seat=claude-web)` / `web-consult` |
| `web-spec` | web-claude | consult brief for dense findings/spec | `team_dispatch(web-consult)` |
| `cursor-sdk-implement` | any seat | server-materialized implement from todo attrs | `generate cursor-sdk source_ref=todo:{slug} contract=implement` |
| `cursor-mechanical` | cursor IDE | skeleton/full disk packet; no web when spec sufficient | `cursor-sdk` default; IDE fallback |
| `cursor-implement` | cursor handoff | implement packet with ACs | fallback: operator opens IDE |
| `operator-gate` | operator | assert template/export; not packet | — |

Canonical pipeline: reasoning upstream → dense artifact + distilled todo attrs → mechanical downstream via `source_ref=todo:{slug}`.

Codified bug/friction defaults to **investigate+decide** before execute: `web-consult` first, GPT-5.5 when corpus self-contained, `cursor-consult` only for Cursor affordances/operator request. Investigate close MUST distill `files_expected`, `acceptance_criteria`, `required_skills` onto bug-fix `todo:` and record implement-ready assertion citing spec + `spec_sha256`. Execute default = cursor-sdk `source_ref` implement. Web-native inline fixes are allowed on web seat; `cursor-implement`/`web-implement` + `packet_path` are named fallbacks. Pass zoom-out duty: inspect beyond filed symptom; closeout has `## Secondary findings`.

Upstream gates (falsifier/operator assert) must close before `web-implement-packet`; otherwise set `workflow_state: blocked` + `block_reason`.

## Gate 2 — stage todo for densification

Operator says “draft preliminary packet for `todo:{slug}` and submit to web” / “stage for densification” ⇒ Gate-2 **consult brief**, never Gate-3 implement packet.

Authority boundary: stager retrieves/scaffolds only. It may summarize constraints, group files, quote assertions, list hypotheses/forks. It MUST NOT resolve design forks, select implementation shape, mark implement-ready, or author Gate-3 implement packet. If judgment is needed, dispatch minimal consult brief with unresolved questions.

Implement-authority boundary: `density_triage=judgment_required` cannot be moved recon→implement by Composer/recon because `implement_ready_preflight.admitted=true`; admission only checks declared-state consistency. Implement-ready assertion must be authored by reasoning tier at Gate-2 densify close. Before self-stamping implement-ready, authoring implement packet, or moving judgment_required item recon→implement, load `consult-routing` § Densify lane.

Triage is declared, not inferred:
- `judgment_required` ⇒ Gate-2 densify; no mechanical implement.
- `mechanical` ⇒ skip densify only with dense source + `required_skills` + context edge + no open forks.
- unset/unknown ⇒ implement blocked; consult/densify only; set triage first.

Sequence:

1. **Verify lane.** `entity_get(todo:{slug}, intent=full)`: require `dispatch_lane ∈ {web-spec, web-implement-packet}`; read `required_skills`, assertions, source signals. Wrong lane/dense spec exists ⇒ say densify is wrong; do not dispatch.
2. **Seed stub spec** `tasks/specs/{slug}.md`: STEP 0 adequacy verdict, Problem/Scope skeleton, `<reasoning_trace>` provenance table, unresolved `§8` forks. Then `entity_update(source_uri=..., workflow_state="in_progress")`.
3. **Author consult brief** `tmp/reviews/{slug}-harden-web-consult-packet.md`: front-matter `contract: consult` + web boot-gate frontmatter; six Gate-2 blocks; `<corpus>` references stub + todo attrs; `<output_format>` demands **dense spec**, not patches; closeout signal `ready-for-Composer-implement`. Label scaffold “retrieval index, non-authoritative; re-derive from primary artifacts; ¬ elaborate unless confirmed.”
4. **Dispatch.** `team_dispatch(op=handoff, role=web-consult, packet_path=..., subject=…)`. Do NOT pass `contract=consult` param; it is not in the enum and returns 422. Consult derives from frontmatter.
5. **Hand back** thread id + `push_reminder`. Gate 3 is later; do not pre-author implement packet.
6. **Gate-2 close distillation (mandatory):** before implement-ready, project dense spec to todo attrs: non-empty `files_expected`, non-empty `acceptance_criteria`, `required_skills` when applicable. Materializer reads attrs only; spec prose is fingerprinted, not content-read. Empty/default attrs reject 422 `implement_attrs_unpopulated` unless waived via `attributes_distillation_waived`.
7. **Entity hygiene:** at stage, seed tracking assertion citing stub + dispatch thread; leave `confidence_band` unchanged. At Gate-2 close, record implement-ready assertion citing dense spec + `spec_sha256:<hex>`, promote confidence as appropriate, distill attrs, keep `workflow_state=in_progress` until Gate 3 completes. Predicate must normalize to `status({todo_id}, implement_ready, current)`; avoid “reopened/in_progress” phrasing or set `predicate_form` explicitly.

## Gate 3 — direct implement dispatch

After Gate 2 closes with distilled attrs:

```python
team_dispatch(op="generate", role="cursor-sdk", contract="implement",
              source_ref="todo:{slug}", dispatch_thread_id="{arc-id}")
```

Materialization consumes todo `files_expected`, `acceptance_criteria`, `required_skills`; spec prose is only fingerprinted.

### Compliance predicate

All required; if any fails, do not wrap.

1. `source_ref=todo:{slug}` resolves intended todo.
2. Active implement-ready assertion cites dense spec and `spec_sha256`.
3. Valid non-empty distilled attrs (`files_expected`, `acceptance_criteria`, plus `required_skills` when applicable).
4. Zero unresolved forks / material branches; `validate_dense_spec` passes.
5. Spec artifact still matches cited `spec_sha256`; otherwise re-validate/re-distill.

### Reject branches

| Condition | Verdict | Action |
|---|---|---|
| Missing attrs | `implement_attrs_unpopulated` | distill/backfill, retry |
| No implement-ready assertion | not ready | return Gate 2 |
| Open fork | not ready | resolve first |
| Spec/attrs drift | `implement_spec_drifted_since_ready` | refresh validation/assertion |
| Need inspect/no-Composer artifact | W4 `contract=wrap` | materialize only |
| Need richer/manual transport | W1 `packet_path`/handoff | hand-authored packet |
| Materializer broken+urgent | break-glass `packet_path` | incident note required |

Wrap is non-remedial. A `source_ref` gate rejection means fix todo/route back, not hand-wrap around it.

Legitimate wrap triggers only:
- W4 inspection artifact: `contract=wrap`, `source_ref=todo:{slug}` materializes packet without Composer.
- W1 alternate/manual transport: `packet_path` or manual `cursor-implement`/`web-implement`.
- W1 non-projectable corpus: executor needs content not representable by attrs; high bar.
- Break-glass materializer incident: `packet_path` + incident note.

NOT wrap triggers: missing attrs, open fork, no implement-ready assertion, multi-todo batch without aggregation/task/plan route, executor-tier override (use `executor_override`).

## Wrap vocabulary

Separate artifact-generation from implementation.

- **W1 lifecycle Gate-3 wrap:** inline hand-authored materialized implement packet; no dispatch.
- **W2 act split:** artifact-generation vs implementation dispatch.
- **W3 `prepare_implement_packet`:** server gate+materialize function.
- **W4 `contract=wrap`:** `team_dispatch(op=generate, role=cursor-sdk, contract=wrap, source_ref=todo:{slug})`; source_ref required, packet_path forbidden, dispatch_thread_id exempt; returns packet_path/provenance without Composer.

Precondition for wrap/dispatch: active implement-ready assertion cites dense spec AND dense spec has zero OPEN forks. Dense check is mechanical via `validate_dense_spec` (required sections, non-empty `<reasoning_trace>`, zero live `OPEN:` markers).

Legacy inline wrap procedure:
1. Read todo + `source_uri`; confirm active implement-ready assertion.
2. Verify dense spec has problem/scope, touched files/functions, steps, ACs, tests, resolved forks/none.
3. Write `tmp/reviews/{slug}-implement-packet.md`: frontmatter `contract: implement`; spec → `<corpus>`; ACs with literal “acceptance” → `<task_guidance>`; six anchored tags.
4. If any fork/gap surfaces, STOP and return Gate 2; do not resolve during wrap.
5. Dispatch implementation by compliant `source_ref` default or named hand-authored `packet_path` exception.

Do NOT dispatch the one-write wrap step to cursor-sdk. `contract=wrap` is materialize-only when needed; `contract=implement` + `source_ref` is Gate-3 default.

## CONFORM / CONVERSE lanes

**CONFORM** (provisional, instrumented): loose intent → conforming todo → Gate 3/wrap. Reasoner authors fork-free intent envelope (`objective`, `touch_points`, `acceptance_criteria_known`, `judgment_settled`, optional `required_skills_hint`); cursor-sdk derives G1–G6 admission structure via `light-bounded` generate against frozen envelope. This is admissible because worker derives structure after judgment is settled; dispatching bare wrap still fails. Verify: Layer 1 wrap precondition gate; Layer 2 bounded semantic diff of `objective`, `acceptance_criteria_known`, `judgment_settled`. Telemetry every run; first-class `contract=conform` blocked until falsifier clears N≥5 real runs.

**CONVERSE** (provisional, lead-run harness): loose intent with latent forks → fork-free intent envelope → CONFORM. Reasoning-capable worker conducts bounded agent-bus clarification dialogue; no one-shot packet/generate. Hard 3-question-round budget enforced by harness. Termination: converge→envelope; budget exhausted→emit residual_open_forks; lead abort→no envelope. Worker must distinguish lead-decided fields from inferred restatements; policy choices not explicitly selected remain residual forks. Telemetry includes dialogue thread, rounds, residual forks, envelope hash, model, CONFORM-admissible?, total lead active-time, fork-value score, and randomized control arm (~30–50%); first-class `contract=converse` blocked until N≥8 real episodes.

## General execution without packet

Schema-free is not direction-free. Fully determinate tasks with pre-authored values may use:

```text
team_dispatch(op=generate, role=cursor-sdk, dispatch_thread_id=…, contract=light-bounded|pure-mechanical)
```

Instructions live on the dispatch thread; `messages[]` is not a parameter. `subject` on generate is ignored; use `to_thread` to set a subject. This lane is distinct from materialized implement packets. Load `cursor-sdk-instruction-standard` before authoring: D1 determinate steps, D2 repeat constraints, D3 self-check, D4 preflight hard-stop.

## Friction-ticket packet preflight

For filed frictions:

| Check | Why |
|---|---|
| Friction ID resolves to `service:*` assertion | avoid task/assertion ID mixups |
| Bound task not already `done` | avoid closed-arc investigate |
| Corpus names exact `entity_id` | avoid service slug drift |
| `<mcp_capabilities>` uses assertion lookup/frictions with same service slug | no guessed service |
| Operator confirmed dispatch intent | typo request ⇒ void protocol |

See `friction-review.md` § Friction ID preflight / Void recall.

## Mandatory preflight before writing packet

For consult and bound implement handoff:

```text
fs(cortex, agent-skills/consult-routing.md)
fs(workspaces, .cursor/rules/architecture-handoff-protocol.mdc)  # The Six Required Blocks
fs(workspaces, .cursor/rules/handoff-dispatchers.mdc)            # target seat
```

Protocol files live at project `.cursor/rules/`, no repo prefix. Skipping because boot had `_CONSULT_ROUTING_GATE` is a violation.

Skill discovery: native on all seats — resident boot index + description-gated stubs (`<available_skills>` / `.cursor/skills` on Cursor; boot manifest on web). Optionally `skill_suggest` for explicit delta-ranking when context shifts. Known skill sets still use mandatory source_uri resolution.

## Skill load resolution

For every skill slug in a packet:

```text
1. cortex(entity_get, id=agent_skill:S) → source_uri (+ digest)
2. Translate source_uri to fs line:
   workspaces://universal-llm-gateway/… → fs(workspaces, path=universal-llm-gateway/…)
   agent-skills/foo.md                 → fs(cortex, path=agent-skills/foo.md)
3. Put translated line in <invariants> or numbered <mcp_capabilities>
¬ derive path from slug alone
¬ use cortex://agent-skills/{slug}.md without entity_get confirmation
```

Path surfaces:
- `packet_path` root = project root; example `tmp/reviews/foo.md`.
- `fs(workspaces)` root = `/mnt/torus/projects`; use `universal-llm-gateway/...` or `projects/.cursor/rules/...`.
- `fs(cortex)` root = cortex sandbox; example `agent-skills/consult-routing.md`.

Do not assume all `agent_skill:*` bodies live at Cortex `agent-skills/<slug>.md`. Put all skill-ref lines in packet turn 1; no bus supplement after dispatch.

Packet-wired ≠ session-loaded ≠ suggested:
- Packet-wired: fs line exists in packet; receiver has not read body.
- Session-loaded: receiver fetched body/listed slug in `loaded[]` or boot preloaded.
- Optional delta: `skill_suggest` may surface not-yet-loaded slugs (confirmatory, not discovery).

A packet-wired slug appearing in optional `skill_suggest` output is confirmatory, not a packet defect. Load packet invariants first.

## Web-receiver priming checklist

For every `team_dispatch(op=handoff, role=web-consult|web-implement, packet_path=…)`: claude-web has MCP but not IDE rules/`.cursor/skills`/terminals. Packet MUST inject what web cannot discover; never post-dispatch supplement.

### Frontmatter boot gate

Required as applicable: `active_project_tag`, `cortex_boot_confirmed: true`, `related_thread_ids`, bound `todo:`/`plan:`. Receiver halts past Gate 2 if missing; `build_pointer_body` reminder is only a backstop.

### Block 2 `<invariants>` skill refs

For ULG repo/code/MCP/events/git-integration/service/routing/pipeline/architecture consults, explicitly include `architecture-invariants.md` and `ulg-architecture.md` resolved from `agent_skill:<slug>.source_uri`; generic web boot does not inject CODING-scope arch bodies. Omit for non-ULG/pure Cortex unless required_skills names them.

Minimum web set:
- `lead-seat-boot.md` or `cortex_boot_confirmed`;
- `consult-routing.md` when consult may close implement-ready;
- ≥1 task-class skill resolved via source_uri;
- all bound work item `required_skills` mirrored.

Task-class examples: MCP/routing `mcp-surface-change`; nontrivial edit `modularize-discipline`; phased/todo `implement-todo`, `implementation-plan-workflow`; pipeline `build-pipeline`, `refine-pipeline`, `debug-with-events`; cursor-sdk message `cursor-sdk-instruction-standard`; lifecycle `service-lifecycle`; dispatch handles `dispatch-shape`; executor advisory `[universal:executor-rec]` in `architecture-invariants`.

### Block 4 `<corpus>` repo pointers

Include explicit `fs(workspaces, op=read, path=…)` for primary spec, pickup/sidecar, every expected file/touch point, and relevant offline tests. Label scaffolds non-authoritative/re-derive.

### Block 5 `<mcp_capabilities>` concrete plan

Numbered plan SHOULD include: boot + skills (native index + packet-wired `source_uri`; optional `skill_suggest` delta); bus/cortex (`agent_bus(fetch)` for each `related_thread_ids`, todo/decision reads); primary code path `fs(read)` steps; live probes for named queues/events. Generic “you have MCP” with <5 concrete steps is an anti-pattern.

### Pre-dispatch self-check

- frontmatter gate fields present;
- ≥1 task-class skill ref via source_uri;
- ULG consult arch pair present when applicable;
- bound required_skills mirrored;
- ≥1 `agent_bus(fetch)` per upstream thread;
- every touch point has numbered `fs(read)`;
- observability probe for named queue/event/live gap;
- behavior-touching spec has event vocabulary or explicit “none needed”;
- scaffold blocks carry no design judgment.

Failure ⇒ complete checklist before dispatch.

## The Six Required Blocks

Author exactly these canonical XML tags, case-sensitive, in order:

| # | Block | Required | Holds |
|---|---|---|---|
| 1 | `<scope>` | yes | reviewed/implemented target, branch/HEAD/path, selection mode |
| 2 | `<invariants>` | yes | compact workspace rules; skill-ref lines + ≤15 task lines |
| 3 | `<task_guidance>` | yes | questions/criteria/work; **acceptance criteria for implement** |
| 4 | `<corpus>` | yes | artifact/context/pointers |
| 5 | `<mcp_capabilities>` | iff dispatcher has MCP | tools, investigation plan, evidence format |
| 6 | `<output_format>` | yes | findings or closeout shape |
| — | `<excluded>` | optional | omitted files/sections + one-word reason |
| — | `<prior_pass>` | optional | iteration preamble |

Implement contract: acceptance criteria live in `<task_guidance>`; closeout evidence in `<output_format>`. Admission rejects implement packets whose `<task_guidance>` lacks the word `acceptance`. Authority via frontmatter `contract: implement` or handoff `contract=` param. Acceptance criteria without contract signal ⇒ `handoff_contract_ambiguous`.

Executor override (implement) may be in frontmatter/request: `executor_override`, `executor_override_reason_code`, `executor_override_reason`. Silence ⇒ `recommended_executor=composer`; see `consult-routing` for R1/R2 policy.

### Block 6 output format by worker tier

- MCP-capable worker: write cortex sidecar `notes/system/threads/{thread}-{subject}.md`; post brief bus pointer with sidecar URI + content_hash/sha256 + one-line summary. Discipline target ≤2KB; server limit 8,000 chars without `allow_long_body`.
- Inline/no-MCP worker (`cursor-sdk`, API generate): emit full closeout inline. Stargate on-behalf delivery writes durable sidecar first and sets `allow_long_body` as needed. Do not tell inline-only worker to write sidecar.

Bus limits: 8,000 chars normally, 64,000 with `allow_long_body`. `allow_long_body=true` only when recipient needs inline long-form and sidecar would break contract.

## Skeleton

```markdown
---
contract: consult   # or implement
---
<scope>
Goal: <one-line>. Selection mode: <targeted|branch|path>.
Primary artifacts: <paths>. Out of scope: <...>.
</scope>

<invariants>
Read before editing:
- fs(... architecture-invariants ...)
- fs(... ulg-architecture ... when ULG)
Per-task narrowing:
| Tag | Rule |
|---|---|
| [universal:no-bc] | delete old surfaces; update consumers same change |
| [scope] | every changed line traces to task |
| [quality] | SLOC gates; load quality gates on code change |
</invariants>

<task_guidance>
For implement: ## Acceptance criteria (numbered, all required).
</task_guidance>

<corpus>
Pointers / artifacts / incident context.
</corpus>

<mcp_capabilities>
Concrete investigation plan; cite tool calls.
</mcp_capabilities>

<output_format>
Findings or closeout table.
</output_format>
```

## Preliminary scaffold → densification

Cheap tier may draft low-judgment scaffold; reasoning tier densifies judgment-bearing blocks. Scaffold carries structure, not conclusions; reasoner authors final dense artifact.

| Block | Scaffold | Densify |
|---|---|---|
| `<scope>` | path list/git SHA/selection | — |
| `<corpus>` | changed-file manifest | — |
| `<invariants>` | skill refs/boilerplate | task narrowing |
| `<output_format>` | closeout boilerplate | — |
| `<task_guidance>` | headers/stubs | all judgment/questions/ACs |
| `<mcp_capabilities>` | tool list | evidence specifics |

Reasoner treats scaffold as fallible candidate; re-derive from primary artifacts. If draft embeds design decisions, prefer either/or tiers or label “candidate, re-derive don’t elaborate.”

## Naming + delivery

- Light/default implement: `team_dispatch(op=generate, role=cursor-sdk, source_ref=todo:{slug}, contract=implement)`; Composer auto-runs; attrs must be distilled. `cursor-implement` handoff is fallback.
- Web Gate-2 closeout: distill attrs, then cursor-sdk generate unless SDK ineligible.
- Legacy/escape hatch: write hand-authored packet before `packet_path=` dispatch.
- `packet_path` is project-root relative (`tmp/reviews/foo.md`), no repo prefix. `fs(workspaces)` needs `universal-llm-gateway/` prefix. Conflating roots causes `handoff_packet_missing`.
- Web handoffs must complete web priming checklist; pointer reminders are backstops.
- Cursor/cursor-sdk packets must include Block 2 skill refs for `architecture-invariants` + `ulg-architecture`, task narrowing, and Block 5 arch-layer reads. Project engineering rules may auto-attach; architecture layer is description-gated, so refs are load-bearing. Do not re-inline auto-loaded generic engineering discipline.
- Bus post is ≤25-line pointer; packet stays on disk.

## Authority

| Topic | Source |
|---|---|
| Six-block contract | project `.cursor/rules/architecture-handoff-protocol.mdc` |
| Dispatcher matrix | project `.cursor/rules/handoff-dispatchers.mdc` |
| Transport routing | `agent-skills/consult-routing.md` |
| Todo dispatch metadata | `universal-llm-gateway/.cursor/rules/todo_ws.mdc` |
| Admission lint | `services/universal-stargate/systems/frontier_consult/handoff.py` |
