---
name: consult-routing
description: "On dispatch-routing — team_dispatch op/role/contract, code vs non-code lane, Gate-2 densify, autonomous work-item spine, authority_fork settlement, or implement_ready gates."
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
`observability`, `panel_dispatch`, `pipeline`, `project_ask`, `team_dispatch`.

Cognitive routing on every seat; CODE_EXTRA = **code MCP only**. On life: in-seat
cognitive legs, `agent_bus` code-seat transport, or honest deferral. Capability
gap: `life-to-code-request-lane` (`lane:life-to-code`).

### `project_ask` (escape-only · deprecation candidate)

Still a **code-primary** today — life seats treat it like other CODE_EXTRA (real
absence on `/mcp/life`; delegate over bus). **Transport:** **escape only** —
prefer `team_dispatch(model=cdp/opus-5|cdp/fable)` (`claude-ai-cdp-navigation`,
`lean-context-dispatch-first`). Reserve bare `project_ask` for satellite-direct,
IF6, and legacy paths. Operator-proxy missions: prefer
`team_dispatch(model=cdp/…, purpose=operator-proxy|mission)`; `project_ask` with
`purpose=` remains a valid escape. ¬ remove
from CODE_EXTRA while it remains on code `tools/list`; ¬ default new workflows to
`project_ask` when CDP `team_dispatch` is available. Full removal = registry +
card derivation change later, not a prose edit alone.

## Dispatch targets (code surface only)

Poll `poll_hint` with `agent_bus(wait)`, not `pipeline(result)`.

| Want | Use | Prompt rule |
|---|---|---|
| Model answer, auto thread | `team_dispatch(op="generate", role=…, contract=…, prompt\|sidecar_ref)` | Atomic prompt; ¬ “reply on this thread” unless handoff. |
| Existing thread | `op="to_thread", thread=…, prompt=…` | Stargate writes turn. |
| Manual seat | `op="handoff", role/seat, packet_path\|source_ref` | “Reply on this thread” OK here. |

Use admit `reply_from_agent` for `wait(from_agent=…)` — ¬ infer from `resolved_model`.

## Code vs non-code (`dispatch_lane`)

SoT: `config/routing/route_policy.yaml`. Substrate-derived — ¬ role proliferation.

| Lane | Signal | Default | Work |
|---|---|---|---|
| **code** | `cursor-sdk` | `generate seat=cursor-sdk` | recon, implement, light-bounded |
| **non-code** | API roles | `generate role=…` or handoff | adversarial, life-domain, analysis |

Settled implement/recon/review → `cursor-sdk` (R2). **Model split:** `implement` → Composer;
IDE/Task breadth recon → **Explore subagent** (`Task(subagent_type="explore")`; ¬ tool);
recon+investigate judgment residual → `cursor/grok-4.5` + `light-bounded`; pure inventory /
Task-unavailable fallback → Composer. `cursor/*` only on `cursor-sdk` → else `422`.

## Bind-then-compose split (judgment closed → nested Composer)

**Invariant:** `judgment_closed ∧ mechanical_remainder ⇒ split_dispatch` — premium / reasoning
models bind; **`cursor/composer-2.5`** implements nested (`seat=cursor-sdk`, omit `model=`).
Rule stub: `bind-then-compose-dispatch_ulg.mdc`.

| Leg | Model / seat | Contract | Delivers |
|---|---|---|---|
| Bind | `cdp/opus-5`, `cdp/fable`, `cursor/grok-4.5`, `cursor/claude-opus-5` (bind scope only) | `light-bounded` | Dense packet / spec: `files_expected`, `acceptance_criteria`, invariants — ¬ repo implement |
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

## cursor-sdk model name surfaces

| Surface | Form | SoT |
|---|---|---|
| cursor-sdk `team_dispatch` | `cursor/{bare}` + knobs | `CURSOR_MODEL_CAPABILITIES` |
| Task subagent | kebab slug | Task roster |
| Cloud API | `provider/model` | `capability_dispatch` |

¬ Task slugs or catalog IDs on `seat=cursor-sdk`. Non-workflow-primary `cursor/*` ⇒ **OPERATOR-GATED**
(`decision:cursor-non-primary-model-operator-gate`).

## Anthropic-family substrate

| Path | Default |
|---|---|
| `anthropic/*` on `team_dispatch` | **PROHIBITED** |
| `cursor/*` on `cursor-sdk` | OK except **Fable** |
| Anthropic consult / binder / R-admit | **`team_dispatch(model=cdp/opus-5\|cdp/fable)`** + `cortex://` staging — poll `poll_hint` |
| `project_ask` (MCP satellite-direct) | **Escape only** — when team_dispatch CDP unavailable, or IF6 / satellite-direct. Operator-proxy: prefer `team_dispatch(model=cdp/…, purpose=operator-proxy)`; `project_ask(purpose=…)` remains valid escape |
| Live checkout | `cursor/claude-opus-*` |

Rule: `anthropic-dispatch-authorization_ws.mdc`. Fable = CDP only (`cdp/fable` / picker — ¬ `cursor/*` Fable).

## xAI coding-substrate

| Path | Default |
|---|---|
| Coding Grok (path-sim **A**, recon+investigate, closed-detent light consult) | `cursor-sdk` + `cursor/grok-4.5` + `light-bounded` |
| Path-sim bundled **Q** (L0) | **CDP Fable** — `team_dispatch(model=cdp/fable)` (`project_ask` `fable-5` = escape; path-sim annex A); ¬ default Grok Q on full arc |
| API `xai/grok-4.5` on coding work | **PROHIBITED** |
| Engineering skeptic | `role=skeptic` + `xai/grok-4.5` |
| Writing / correspondence | Grok **PROHIBITED** — L3 annex |

## Implement lane — default source_ref

```python
team_dispatch(op="generate", seat="cursor-sdk", contract="implement", source_ref="todo:{slug}", dispatch_thread_id="{arc-id}")
```

Materializer reads attrs only; spec prose = hash input. Preflight: `entity_get`; `workflow_state ∈ {open,in_progress}`.
`wrap` = materialize-only. Contract↔source matrix: L3 annex.

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

`recon → settle → densify → GPT merged check → Composer`

One merged GPT check. Gate-6 substrate, zoom-out, seeding ladder, overhaul, `authority_fork`: L3 annex § spine extensions.

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

Peer `/path-sim`. Attrs: `bind_status∈{unsettled,settled,shipping,deferred}`, `workflow`, `next_action`.

| # | Condition | Route |
|---|---|---|
| 1 | `deferred` | held |
| 2 | `settled\|shipping` ∧ ¬`recon_pending` | **ADDRESS** |
| 3 | `unsettled` ∧ `judgment_required\|recon_pending` | **PATH-SIM** |
| 4 | `mechanical` ∨ (`implement_ready` ∧ stamped) | **DISPATCH** |
| 5 | else | **PATH-SIM** |

Gate-2 sets `bind_status=settled`. Mirrors cite `SOT: consult-routing § Address`.

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
