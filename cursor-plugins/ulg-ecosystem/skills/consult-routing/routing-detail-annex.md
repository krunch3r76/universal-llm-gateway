# Consult routing — detail annex (L3)

Load on demand for matrices, provisional lanes, and catalogs relocated from L2.

## cursor-sdk contract ↔ source-shape matrix

Authoring-time map for `seat=cursor-sdk` `op=generate` — do not mix shapes:

| `contract` | Allowed source | Forbidden |
|---|---|---|
| `implement` | `source_ref` **or** `packet_path` | — |
| `light-bounded` | `packet_path` **or** bus-turn body | `source_ref` (except conductor spawn — next row) |
| `light-bounded` + `packet_kind=conductor` | `source_ref=todo:{slug}` only | `packet_path`, `prompt`, `sidecar_ref` |
| `pure-mechanical` | `packet_path` **or** bus-turn body | `source_ref` |
| `wrap` | `source_ref` only | `packet_path` |

**Conductor spawn:** Stargate materializes from `source_ref`; kickoff body = materializer
output — never `sidecar_ref` (or `prompt`) beside `source_ref` (`multiple_prompt_sources`).
See `agent_skill:conductor` § First-utterance spawn.

Foot-gun: `contract=light-bounded` + `source_ref` without `packet_kind=conductor` is invalid
(agent-bus:4866). Do not paper over conductor spawn with `sidecar_ref`.

## Writing consult substrate

Work class: outbound letters, correspondence, prose critique+rewrite. Complements `prose-discipline`.

| Model / path | Rule |
|---|---|
| `xai/grok-4.6` / `cursor/grok-4.6` | **PROHIBITED** for writing |
| `openai/gpt-5.5` | **OPERATOR-GATED** |
| Standing writing multi-model | `role=reviewer` → `openai/gpt-5.6-terra` + `role=synthesizer` → Gemini; ¬ default `panel_dispatch` |
| Lead / web-anthropic in-seat | OK when corpus staged |

Entity: `decision:writing-consult-model-routing`.

## Abstraction layering — cost bind (operator 2026-07-27)

After frontier architecture verdict: ¬ web-Opus full dense spec with inlined `skills=`.

| Layer | Seat | Delivers | Must NOT |
|---|---|---|---|
| Architecture | Fable / wide CDP | Target shape, rivals, migration | File matrix, phase ACs |
| Frame | Opus CDP (minimal `skills=`) | Grok instruction brief ≤~120L | Spec body, workspaces reads |
| Densify | Grok `cursor-sdk` `light-bounded` | `cortex://…/specs/{slug}.md` | Re-litigate architecture |
| Check | GPT terra/Sol `cursor-sdk` | Merged consistency pass | — |
| Implement | Composer `cursor-sdk` `implement` | Phase-scoped edits | Redesign kernel |

Opus frame output: `cortex://…/{slug}/opus-grok-instructions.md`. Grok densify: `sidecar_ref` = brief;
`skills=[implementation-plan-workflow, architecture-invariants, ulg-architecture, modularize-discipline, …]`.

**Architecture BIND / service-home packets (CDP Fable or Opus):** attach judgment pair **and**
inline `architecture-invariants` + `ulg-architecture` (both `cursor_only` — CDP inject adds a
read cue; life Customize does not carry them). When process manager /
service home / extract is load-bearing, **inline** `[ulg:host-process]` one-liner (manage
subprocesses except satellites; repo systemd ≠ live). Detail: `claude-ai-cdp-navigation`
reference-annex packet-class row *ULG service home / placement / extract / hosting BIND*.

**Anti-pattern:** Fable → Opus full densify with 6+ inlined skills = layer collapse.

## Autonomous work-item spine — extensions

**Gate-6 substrate (a24082):** code-lane live-source / `workspaces://` citations ⇒
`team_dispatch(op=generate, seat=cursor-sdk, model=cursor/gpt-5.6-terra|sol|luna|cursor/grok-4.6,
contract=light-bounded, …)`; poll `reply_from_agent` from admit. API `role=reviewer` + terra only when
**all** reading pre-staged inline (`code-on-api`). Access-only REVISE ≠ Gate-6 close.

**Steps 1–2 zoom-out (C2):** recon/investigate packets MUST carry touch-point inventory + class/sibling
grep + `## Secondary findings` (or `None observed.`). Template:
`cortex://notes/system/templates/recon-investigate-packet.md`.

**Optional Gate-2 seeding ladder** (opt-in only): thin_seeder → strategic_framer → densify_adjudicator →
[rare] Fable → GPT check → Composer. Detail: todo-lifecycle § Seeding ladder.

### authority_fork STOP

∀ settlement packet: fork touching {provider default model ∨ `anthropic/` identity ∨ product/catalog identity ∨
external-counterparty artifact ∨ money-/risk-moving config ∨ irreversible deletion} ⇒ `authority_fork`, escalate.

### Overhaul-seat posture

Orchestrator MAY be web Sonnet (or cursor Grok/Sonnet) driving the spine. **Forbid** web mid-pipeline
densify/check/implement insertion. Escalation terminal retains Opus/Fable for `authority_fork` / deadlock /
contested non-code / pre-codification ratification only.

### Ratify-before-codify

Promotion of spine / Gate-2.5 policy into this skill requires interactive ratification. Post-hoc: sampled
conformance/drift audit every 5 autonomous closes or each CHECKPOINT.

## Executor tier and handoff mechanics

Seat/role and executor tier are orthogonal. Manual seats admit only `op=handoff` + `source_ref|packet_path`.

- **R1 reasoning/spec:** judgment, design, root cause, dense-spec authorship → consult/generate/manual.
- **R2 settled implement:** fork-free dense spec + attrs → default `cursor-sdk` `contract=implement`.
- **R3 bounded widened discovery:** sibling/same-class defects; label secondary findings; ¬ open-ended redesign.

| Axis | `cursor` handoff | `cursor-sdk` generate |
|---|---|---|
| Attendance | Operator at IDE required | Unattended |
| Authorization | Explicit-only | Pre-trusted implement/light mechanical |

“Separate thread / stay lean / autonomously investigate” ⇒ `cursor-sdk`, not attended `cursor`, unless operator
explicitly chooses `cursor`.

## General execution lane (no full packet)

```python
team_dispatch(op="generate", seat="cursor-sdk", dispatch_thread_id="<thread>",
              contract="light-bounded"|"pure-mechanical", packet_path?=...)
```

Load `cursor-sdk-instruction-standard` (D1–D4). Repo-venv: cursor-sdk inherits repo venv. Inline lead edits =
one exact judgment-authored edit only; multi-edit mechanical work ⇒ cursor-sdk light dispatch.

### cursor-sdk write channels

`op=generate` writes closeout sidecar (A); may write durable deliverables via MCP `fs` when D0 names path (B);
shared-checkout mutation (C) needs `contract=implement` or attended handoff. Verify channel-B by
`written_sha256` or read-back. Empty `0 tool calls / ~2.8s / 0B` closeout = credit exhaustion.

### Post-dispatch output mutation gate

∀ cleanup on prior dispatch outputs: confirm executor **TERMINAL** first. Proof: `agent_bus(wait)` `complete=true`
+ `terminal_status ∈ {completed,failed}`; or posted closeout; or `pipeline(result).status ∈ {complete,failed,cancelled}`.
Outer `CURSOR_SDK_TIMEOUT` ≠ terminal. RAG ingest batches: default `scripts/ingest-article` **without** `--index`.

## CONFORM lane — provisional

Loose intent → conforming todo. Recipe: `team_dispatch(generate, seat="cursor-sdk", contract="light-bounded",
packet_path=<frozen-envelope>)`. Envelope: `objective`, `touch_points`, `acceptance_criteria_known`,
`judgment_settled`, optional `required_skills_hint`. Verify Layer 1 wrap precondition + Layer 2 semantic diff.
Promotion blocked until N≥5 real runs.

## CONVERSE lane — provisional harness

Latent-fork clarification → envelope → CONFORM. Lead-run; hard 3-question-round budget. Termination: converge /
budget exhausted (`residual_open_forks`) / lead abort. Promotion blocked until N≥8 episodes with control arm.

## Provider affordances vs roles

Role = contract/briefing. Optional `model=` selects within role. Read `capabilities` / `panel_capabilities`
before prompt composition.

## Pointer-only corpus (MCP-on receivers)

`receiver_has_mcp ⇒ corpus = pointers_only` on hand-authored packets. Life/web: Use `life-handoff-corpus` skill.
Authoring depth: `handoff-packet-authoring` § Life-surface cortex-mirror gate. Code MCP: `workspaces://`,
`cortex://`, entities, `agent-bus:` allowed; ¬ inlined dumps when receiver can fs-read.

## Source shapes

`source_ref` schemes: entity, packet, agent-bus. Entity normalizer reads attributes only — spec prose is
fingerprinted, not instruction source. `packet:{path}` trusts six-block packet; `validate_packet` gates.
`cortex://notes/system/specs/{slug}.md` reaches executor via entity materialization or hand-authored packet.

## Vision-align / Events-probe (pointers)

Path-sim Q/A/R + `judgment_required` densify closeouts: include footers per
`cortex://notes/system/specs/vision-align-grammar.md` (block grammar + globs); R-admit
machinery remains in `path-sim/substrate-staging-annex.md`. § Events/gap probe.
Specs: `cortex://notes/system/threads/ulg-vision-align-g4-densify.md`,
`cortex://notes/system/threads/ulg-path-sim-events-g5-densify.md`.

## Task-class model reference

Advisory — operator may override. `route_policy.yaml` does not machine-encode this table.

| Task class | Family | Effort | Thinking |
|---|---|---|---|
| Acks/status, routine assertions, session-close, lint fixes | Sonnet 5 | Low | off |
| Reading/summarizing, following ready spec | Sonnet 5 | Medium | on |
| Large-corpus read/summarize/extract | Gemini 3.5 Flash | — | — |
| Single-subsystem review/debugging | Sonnet 5 | High | on |
| Cross-agent protocol; shared Cortex/session-close/agent-bus | Opus 5 | Low | on |
| Multi-subsystem review / large diff | Opus 5 | Low | on |
| Cortex schema, event vocabulary, domain modeling | Opus 5 | Low | on |
| Adversarial/dialectical; high-rework; two failed attempts | Opus 5 | High | on |
| Cross-family second perspective | GPT-5.5 | High | on |
| Opus Extra truncated; ceiling quality, cost not constraint | Fable 5.1 | High/Max | on |
