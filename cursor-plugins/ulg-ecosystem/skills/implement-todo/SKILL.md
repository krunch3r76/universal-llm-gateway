---
name: implement-todo
description: "On /todo pickup or judgment_required Composer admit — readiness, routing, and Gate-6→FILE_EVIDENCE→skeptic_ratified|gate6_ratification_uri|recon_waived→preflight recipe."
trigger_match_terms: ["implement-todo", "implement_todo", "implement", "pick", "todo", "admit", "preflight", "skeptic_ratified", "recon_waived", "gate6_ratification_uri", "FILE_EVIDENCE", "execute"]
---

# Implement Todo

Single-todo pickup readiness + routing gate.

## Trigger / boundary

Fire on `/todo pickup {slug}`, `Pick up todo:{slug}`, “implement/execute/pick up/do `todo:*`,” or resuming action on one `todo:` entity.

`single_todo_pickup ⇒ load(this_skill)`.

`is_plan_arc(todo) ⇒ stop ∧ load(implementation-plan-workflow)`. This skill does not coordinate multi-phase plans; apply `entity-lifecycle-discipline` Todo→Plan thresholds in Step 2.

## Core invariant

A todo is not a license to type.

`pickup(todo) ⇒ verify_live ∧ load_governing_skills ∧ gauge_readiness ∧ classify_route ∧ ((safe ∧ READY) ⇒ proceed) ∧ ((gated ∨ risky ∨ cross_seat) ⇒ checkpoint)`.

Half-implementing a gated todo, or implementing one already done/superseded, is worse than not starting.

## Companion skills

- `reasoning-posture` — verify-don't-assume / self-correction.
- `entity-lifecycle-discipline` — Todo→Plan thresholds, todo-vs-plan scope, `workflow_state` semantics.
- `implementation-plan-workflow` — plan deck authoring / `/implement-plan` coordination after threshold trip.
- `dispatch-workflow` / `consult-routing` — DISPATCH mechanics + gate contract detail.
- `todo-lifecycle` — Gates 4–8 lifecycle wrapper around densify/check/implement.
- `cheap-recon-before-escalation` — axis-2 skeptic dispatch + `FILE_EVIDENCE_PATHS` footer.

## Protocol

### 1. Verify live from primary source

`entity_get(todo, intent="full")`; read sidecar if present. Confirm:

- `workflow_state ∈ {open, in_progress}`;
- not `done`;
- not superseded.

`boot_card ∨ bus_turn ⇒ secondary_record`; `stale_secondary_record ⇒ re_read_primary_before_acting`.

### 1b. Load governing skills before readiness

Readiness and route are judged through governing skills.

`S = attributes.required_skills ∪ requires_edges(agent_skill:*)`.

Per slug:

1. `entity_get(agent_skill:{slug})` — metadata only if needed.
2. **Use the** `{slug}` **skill** — canonical slug; seat self-fetches body. ¬ fs-read skill markdown.

Set handling:

- `S ≠ ∅ ⇒ load_all_by_canonical_slug(S)`.
- `S = ∅ ∧ ULG_repo_work ⇒ load skills: architecture-invariants, ulg-architecture, docstring-quality, event-instrumentation-discipline`.
- `S = ∅ ∧ ambiguous ⇒ checkpoint_operator_with_skill_candidates`; do not guess or proceed READY.

`required_skills` is a floor. After floor, scan task-relevant extras via repo skills README, boot manifest rows, or cortex skills README. Read only trigger-matching extras. If a todo has empty required skills, prefer backfilling attribute + `requires` edges for next pickup.

**Posture layer (vision) above the architecture floor.** The ULG code-work floor
(`architecture-invariants`, `ulg-architecture`, `docstring-quality`,
`event-instrumentation-discipline`) is mandatory. On `judgment_required` or
**pillar-touching** pickups — pull rubric from GET `/api/v1/doctrine/vision-digest`;
escalate via `sot_uris[]` / full MAP read only at wide detent — and carry a
`VISION-ALIGN` footer per `cortex://notes/system/specs/vision-align-grammar.md`
(`/layer` G6 check in `abstraction-layering`). This is a **choke-point cue,
not a `required_skills` add**; mechanical/trivial leaves skip it. Vision digest extends
the floor — it never replaces it.

### 2. Scope-check, then gauge readiness

First apply `entity-lifecycle-discipline` Todo→Plan threshold. Promote/exit when ≥2 indicators fire: ≥3 confirmed assertions on distinct sub-concerns; ≥2 `related_to` sub-item todos; natural independently shippable phases; conjunction-heavy name; spec spans multiple deliverable sections.

If not a plan arc, classify exactly one:

| State | Meaning | Action |
|---|---|---|
| READY | pinned scope, enumerated surfaces, concrete first action, acceptance criteria, no unresolved upstream decision | route |
| GATED | blocked on owner/operator decision, upstream todo, or unresolved convention | stop; report missing gate |
| UNDER-SPEC'D | scope/surfaces not pinned | stop; request/specify missing info |

`todo.READINESS section present ⇒ authoritative`.

### 2b. Gate-2 densify closeout (when densifying)

Fire when this seat densifies a todo toward `implement_ready` **outside** a
path-sim Stage-A worker packet — including split-phase densify, operator
**Proceed** that would stamp readiness, or any hand-authored dense note.
Bundled path-sim Stage-A still owns its ordered Gate-2 recipe (Use the
`path-sim` skill § Stage-A Gate-2 densify closeout); this § covers
implement-todo densify/Proceed surfaces.

1. **Densify START (hard):** `cortex(doc_template, doc_type=implement_dense_spec)`
   → write/overwrite `cortex://notes/system/specs/{slug}.md` **before** any
   dense prose. Freeform “numbered sections / Bound forks / Files expected”
   notes are **not** a dense spec.
2. Fill the 8 template sections + `<reasoning_trace>` (accepted heading phrases —
   Use the `handoff-packet-authoring` skill).
3. **Before `implement_ready` / operator Proceed:** `cortex(doc_validate, path=…)`
   until gates **6/8/9 PASS** (`authoring_mode`); cite returned
   **`attestation_tokens`** (or equivalent attestation evidence) on the
   readiness stamp path.
4. Distill non-empty `files_expected` + `acceptance_criteria`; stamp
   `implement_ready` with `spec_sha256:` of the validated body.
5. **Independence note (BINDING):** `fs`-readable path and dense-spec **schema**
   are **independent** checks. Path exists / body loads ⇏ schema PASS. Gate
   codes may co-occur across a failure episode (e.g. `implement_spec_unreadable`
   when the body cannot be loaded for validation vs schema fail when a loaded
   body is freeform / `implement_spec_not_dense`) — treat both; do not equate
   “I can read the note” with implement-ready.

### 3. Classify route

Pick the cheapest sound route for this seat. **SOT:** consult-routing § Address (bind_status chooser).

| Route | Use when | Mechanism |
|---|---|---|
| DIRECT | cortex/skill/doc codification, graph edits, sidecars; reversible, no shared-tree mutation | `fs(cortex)` + cortex ops |
| **ADDRESS** | **`bind_status∈{settled,shipping}`** ∧ **`density_triage≠recon_pending`** | **`/address`** — ship/advance settled binds; `entity_update` + `merge_state_card` for stage/`shipping` advances |
| **SEED** | no closable `todo:` ∧ codework | **`/work-item-seed`** S4a then spawn — Use the `work-item-seed-path` skill |
| **LAYER** | **`bind_status=unsettled`** ∧ **`density_triage∈{judgment_required,recon_pending}`** ∧ codework; default when unmatched codework | Re-admit conductor at highest open G — Use the `abstraction-layering` skill for **gate shape**; `/layer` is not a second admit; **defer §3b** unless `check_requested=true` |
| **PATH-SIM** | same bind ∧ (**non-codework** ∨ `arc_lane=path_sim` ∨ operator named `/path-sim`) | `/path-sim` (bundled) — Use the `path-sim` skill § Bundled dispatch |
| DISPATCH | **`density_triage=mechanical`**; or **`implement_ready`** ∧ dense spec after Gate-2; explicit post-densify implement after opt-in Gate-6 | `team_dispatch(op=generate, seat=cursor-sdk, contract=implement, source_ref=…)` |
| COORDINATE | operator/other seat needed; dirty-tree commit; cross-seat changes; owner ratification | ask/operator or agent_bus handoff |

`bind_status=deferred` ⇒ **held** (no route; `next_action=await_unblock`).

`doc_or_cortex_codification ⇒ DIRECT`; no todo ∧ codework ⇒ **SEED** (S4a → spawn); **`unsettled` + `{judgment_required,recon_pending}` + codework ⇒ LAYER** (re-admit conductor; ¬ ADDRESS, ¬ default PATH-SIM); same bind + PATH-SIM trigger ⇒ **PATH-SIM**; **`settled`/`shipping` (≠ `recon_pending`) ⇒ ADDRESS**; `mechanical ∨ (implement_ready ∧ dense_spec) ⇒ DISPATCH`; `dirty_tree_commit ∨ cross_seat ∨ owner_gate ⇒ COORDINATE`; unmatched codework ⇒ LAYER (conductor).

SOT: consult-routing § Address

Routes are for one bounded pickup only. If you are deriving per-phase routes, you missed Step 2; exit to `implementation-plan-workflow`.

### 3b. Judgment_required DISPATCH admit recipe — **opt-in only**

**Default:** `judgment_required` codework ⇒ **LAYER route (§3)** (re-admit conductor) — skip this section.
PATH-SIM only on the §3 trigger set (non-codework / `arc_lane=path_sim` / named `/path-sim`).

**Fire §3b only when:** `attributes.check_requested=true` on the todo, operator explicitly requests Gate-6/API check, or post-path-sim follow-up `contract=implement` after split-phase densify (legacy spine).

`DISPATCH ∧ contract=implement ∧ density_triage=judgment_required ∧ check_requested ⇒ finish this checklist before team_dispatch`. Gate contracts: consult-routing § Implement admission gates; lifecycle: todo-lifecycle §6–7.

**Gate-6 substrate (friction a24082):** before dispatching the check — if the packet cites `workspaces://` or needs live-code verify ⇒ `seat=cursor-sdk` + `model=cursor/grok-4.6` (Cursor Models default), `contract=light-bounded`. `cursor/gpt-5.6-terra|sol|luna` only if the operator/packet **names** Other Models. API `role=reviewer` only when every required artifact is inlined (`code-on-api`). Access-only REVISE (missing fs/checkout / note-body 404) ⇒ re-dispatch on cursor-sdk; ¬ Gate-6 close; ¬ Composer. Densify `implement_ready` ≠ ratification. Bound: address the SDK peer via `seat=cursor-sdk` on `op=generate` (role≠substrate; `todo:team-dispatch-role-substrate-cohesion`).

**Happy path**

| Step | Action |
|---|---|
| 1 | Confirm active `implement_ready` + distilled `files_expected`/`acceptance_criteria` + current dense-spec `spec_sha256`. |
| 2 | Dispatch Gate-6 on the substrate above; ensure the ratifying turn body carries literal `FILE_EVIDENCE_PATHS:` (bare `workspaces://`/`cortex://` paths, no bullets). Spine home = GPT merged-check turn. |
| 3 | Assert `status(todo, skeptic_ratified, current)` **confirmed**, `evidence_uris` = [`agent-bus:{tid}#turn-{N}` of that FILE_EVIDENCE turn, `spec_sha256:<current hex>`]. Grounding reads the **first** `agent-bus:` URI — ¬ cite orchestration root / densify WIP. |
| 4 | `cortex(tool="implement_ready_preflight", arguments='{"source_ref":"todo:{slug}"}')` → `admitted=true` (empty gate-13 warnings, or fix named code). |
| 5 | `team_dispatch(op=generate, seat=cursor-sdk, contract=implement, source_ref=todo:{slug})`. |

`FILE_EVIDENCE_PATHS ∈ Gate-6 turn body ⇏ Gate-13 pass`. Absent the `skeptic_ratified` assertion citing that turn → `skeptic_pass_missing` / `skeptic_evidence_missing` (incident: a23903 + 4917#110).

**Gate-6 alternate path** (landed 2026-07-13, `resolve_gate6_ratification`; friction 24001 follow-up): when no `skeptic_ratified` row and no `recon_waived`, set todo attribute `gate6_ratification_uri=agent-bus:{tid}#turn-N` designating the exact Gate-6/check turn. That turn must carry an authoritative verdict line (`## Verdict: **RATIFY**` or `**Verdict:** **RATIFY-WITH-CONDITIONS**`), the literal `spec_sha256:<current hex>` token, and a resolvable `FILE_EVIDENCE_PATHS:` block. `implement_ready` must still cite the current `spec_sha256:<hex>` in `evidence_uris`. Preflight/Gate-13 treat the designated turn as effective ratification — ¬ auto-mint a graph `skeptic_ratified` assertion; ¬ use the first `agent-bus:` on `implement_ready` as the ratifying locus.

| Step | Action |
|---|---|
| A1 | Same as step 1 (active `implement_ready` + current `spec_sha256`). |
| A2 | Confirm Gate-6 turn has `FILE_EVIDENCE_PATHS:` + affirmative verdict + `spec_sha256:<hex>`. |
| A3 | Set `attributes.gate6_ratification_uri=agent-bus:{tid}#turn-{N}` (explicit `#turn-N` required). |
| A4 | `implement_ready_preflight` → `admitted=true` with gate-13 evidence grounded. |
| A5 | `team_dispatch(op=generate, seat=cursor-sdk, contract=implement, source_ref=todo:{slug})`. |

**Waiver path** (stamp unavailable; same pattern as sw-imprint-commit densify): set `attributes.recon_waived` to a **JSON string**:

`{"waived_by":"<seat>","reason_code":"design_pre_adjudicated","reason":"<≤1 sentence>","spec_sha256":"<current hex>","waived_at":"<ISO>"}`

`reason_code ∈ {ratified_on_prior_spec_revision, design_pre_adjudicated, operator_directive, path_sim_self_certify}`. Waiver `spec_sha256` must match the **current** dense-spec hash or the gate discards it as stale. Re-run preflight.

| Preflight code | Fix |
|---|---|
| `skeptic_pass_missing` | Write/repair `skeptic_ratified` with `#turn-N` + literal `spec_sha256:<hex>`, or set `gate6_ratification_uri` per alternate path above |
| `skeptic_evidence_missing` | First `agent-bus:` URI must be the FILE_EVIDENCE turn (or re-emit footer there) |
| `skeptic_evidence_unresolved` / `_malformed` / `_stamp_missing` | Re-emit the footer on the ratifying turn as bare scheme-prefixed paths — no bullets, no `- `, blank-line terminated (format: cheap-recon § skeptic footer) |
| Legacy/malformed Gate-6 turn (bulleted block, non-literal verdict — e.g. 4917#110) | Re-emit a corrected turn and designate it via `gate6_ratification_uri`, or fall through to the waiver path |
| stale waiver | Refresh `recon_waived.spec_sha256` to current hash |

**Reviewer corpus path (a23998):** `cortex(resolve)` resolves **entity** URIs (`cortex://type/slug`), not note file bodies. `cortex://notes/...` → 404 (`Entity not found: notes:…`). Dense-spec/sidecar bodies: `fs(op="read", path="cortex://notes/...")`.

### 4. Act or checkpoint

Proceed autonomously only on `READY ∧ (DIRECT ∨ read_only_low_risk_DISPATCH)`.

`judgment_required ∧ contract=implement ⇒ §3b complete before DISPATCH` **only when `check_requested`**.

**LAYER default (§3) for codework:** §3b skipped. PATH-SIM A-bind + `path_sim_self_certify` only on the §3 PATH-SIM trigger set.

Checkpoint first on `COORDINATE ∨ code_mutating_DISPATCH ∨ irreversible ∨ cross_seat`.

State the chosen route before any irreversible step.

### 5. Close out

`close ⇒ evidence_cited ∧ workflow_state_advanced ∧ graph_coherent`.

**Work complete = go live (BINDING — operator 2026-08-25):**
`close ⇒ commit(work paths) ∧ (ordinary_live_proven ∨ liveness_n/a) ∧ stamp`.
Finishing the item **is** the loop — not a later "go live" ask.
`¬live@sha ⇏ withhold`; `¬proven(live) ⇒ prove, ¬park`. Closure assertion
states claim class and, when `¬live@sha`, the reason (which served paths,
whose WIP). Mid-arc checkpoint commit without claiming done stays commit-only.
`decision:go-live-proof-loop` assertions 30579 (stamp) / 30577 (recycle) /
30584 (loop) / 30585 (done opens the loop).

Include dispatch id + result line for DISPATCH; entity/assertion ids for DIRECT. Update `workflow_state`, write backing assertions with `evidence_uris`, and wire provenance edges.

**Docstring ship gate (BINDING — path-agnostic):** when the pickup mutated **public** Python surface (module/class/public fn), before todo-close:

1. `scripts/docstring-quality scan|check` on `files_expected` (or touched `*.py`).
2. **criticals=0** — cite scan path + exit in closeout evidence.
3. Concentrated warnings ∨ arch/RAG feedstock needed → `/docstring-enhance` (CDP Sonnet) → re-scan.
4. Path-sim or not: this gate still fires (Use the `docstring-quality` skill § Ship gate).

`¬ close` with empty/critical docstring debt on new public surface. Mechanical inventory-only / no public Python touch ⇒ n/a (state why).

**Event instrumentation closeout (BINDING — judgment, not a scan):** when the pickup touched behavioral edges or `@event_factory` emit sites, closeout states in one line — events added (`signal` · `role` · why) OR "no event warranted (reason)", plus any prune/relabel candidates spotted (Use the `event-instrumentation-discipline` skill). No criticals scan — add/prune is judgment. Silence on an event-bearing change is the miss.

**Session / work review (optional, recommended):** on judgment-bearing ship, cue
`team_dispatch(model=cdp/opus-5, purpose=review)` (`consult-routing` § CDP
transport). Background preferred; defer and name it when attended-blocking.
¬ a close gate; ¬ silent Terra G4; ¬ a substitute for path-sim R-after.

## Anti-patterns

- Readiness/routing before governing skills.
- Starting from boot card or bus turn without re-reading entity.
- Implementing `done` or superseded todo.
- Forcing GATED/UNDER-SPEC'D work to look productive.
- Over-routing DIRECT work to DISPATCH/COORDINATE; under-routing dirty-tree/cross-seat work.
- Mutating another seat’s in-flight work without coordination.
- Closing without graph bookkeeping.
- Closing a public-Python pickup without docstring-quality scan criticals=0 (path-sim or not — §5 Ship gate).
- Closing an event-bearing change silent on instrumentation (add/prune judgment — §5 event closeout).
- Firing `contract=implement` on `judgment_required` after Gate-6 FILE_EVIDENCE without stamping `skeptic_ratified` (or a designated `gate6_ratification_uri` turn, or hash-matched `recon_waived`).
- Using `cortex(resolve)` on `cortex://notes/...` bodies instead of `fs(read)`.
- Authoring a freeform dense note (or rewriting template headings by hand) instead of starting from `doc_template(implement_dense_spec)` (§2b).
- Stamping `implement_ready` or asking operator Proceed without `doc_validate` PASS + attestation citation (§2b).
- Treating `fs(read)` success on `source_uri` as schema/readiness proof (`implement_spec_unreadable` and schema failure are independent failure modes — §2b item 5).

## Prior-override examples

| Case | Correct classification |
|---|---|
| Codify a settled `decision:` + skill edits via `fs(cortex)` | DIRECT |
| Read-only cursor-sdk verification, `21 passed, 6 warnings in 0.52s` | DISPATCH; autonomous |
| Phase 4 squash commit from web seat with no git primitive | COORDINATE |
| Todo blocked on owner call despite partial ready design | GATED; report, do not force |
| Bus says remaining but primary assertion shows superseded/done | Primary source wins |
| Gate-6 turn has FILE_EVIDENCE but preflight `skeptic_pass_missing` | Stamp `skeptic_ratified` citing that `#turn-N` + `spec_sha256` (§3b), or set `gate6_ratification_uri` (§3b alternate), or `path_sim_self_certify` waiver after path-sim A bind |
| `judgment_required` todo at pickup | Default **LAYER** (codework) — skip §3b unless `check_requested=true`; PATH-SIM only on §3 trigger set |
| Need Composer without fresh skeptic stamp; path-sim A bind complete | `recon_waived` with `path_sim_self_certify` + matching `spec_sha256`, then preflight (§3b waiver path) |

## Minimal operating summary

1. Re-read todo entity; confirm open/in_progress and not done/superseded.
2. Load governing skills: `required_skills ∪ requires→agent_skill:*`; prefer `source_uri` workspaces SOT; default ULG pair only for ULG repo work; checkpoint if ambiguous.
3. Apply Todo→Plan threshold; if plan arc, exit to `implementation-plan-workflow`.
4. Classify READY/GATED/UNDER-SPEC'D; stop unless READY.
5. When densifying toward ready: §2b (`doc_template` start → `doc_validate` PASS → then `implement_ready` / Proceed).
6. Route DIRECT/**LAYER**/SEED/PATH-SIM/DISPATCH/COORDINATE; **LAYER** is default for codework `judgment_required`; PATH-SIM only on §3 trigger set; §3b only when `check_requested`.
7. Close with evidence and coherent Cortex bookkeeping.
8. Public Python touch ⇒ docstring-quality scan criticals=0 cited (§5) before todo-close.
