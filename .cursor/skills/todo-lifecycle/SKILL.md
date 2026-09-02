---
name: todo-lifecycle
description: "On any work item from todo seed to verified ship — gate order, triage/densify, panel gates, implement dispatch, and what blocks the next gate."
trigger_match_terms: ["todo-lifecycle", "todo_lifecycle", "todo", "lifecycle", "gate", "seed", "triage", "density_triage", "densify", "dense spec", "recon", "panel", "skeptic", "ratification", "implement dispatch", "verify", "ship", "seed to ship", "which gate", "gate blocked"]
related_skills: ["rule:todo-lifecycle", "implement-todo", "consult-routing", "orchestrator-workflow", "handoff-packet-authoring", "consensus-steelman-posture", "cheap-recon-before-escalation", "entity-lifecycle-discipline"]
---

# Todo Lifecycle — seed to verified-done

The gate order for a work item from seeded `todo:` to a `VERIFIED COMPLETE` ship, plus the operator directives and live traps. This is the **order and the gotchas**, not a restatement of mechanics — each gate points to the skill that owns its depth.

## Trigger / boundary

Fire when orienting on where a work item sits in the arc, or advancing it to the next gate.

- `single_todo_pickup_readiness ⇒ implement-todo`. That skill owns READY/GATED/UNDER-SPEC'D classification and DIRECT/DISPATCH/COORDINATE routing for **one** entity; this skill owns the **cross-gate arc**.
- Reference layer — entity schema, two-tier seed contract, grouping (`task:`/`plan:`/`project:`), dispatch attributes, completion pipeline, session-close reconciliation — lives in `rule:todo-lifecycle`. Cross-link; ¬ restate.

## Core invariant

`advance(todo, gate_n → gate_{n+1}) ⇒ prior_gate_evidence_live`. Every gate has an explicit admission precondition, and a skipped gate blocks a later one **silently**: `density_triage` unset ⇒ implement 422; skeptic evidence absent ⇒ implement 422. A gate is passed only when its backing assertion/evidence is current — not when the narrative reads done.

**Default execution shape (`judgment_required` code-lane):** the autonomous work-item spine (`decision:autonomous-work-item-spine`; consult-routing § Autonomous work-item spine):

```
recon → settlement/escalate → densify → GPT merged check → Composer implement
```

Orchestrator MAY be web-anthropic **Sonnet** (or cursor). Mid-pipeline web densify/check/implement **forbid**. Higher-tier web = escalate only (`authority_fork` / deadlock / pre-codify). Mechanical todos skip spine ceremony.

**Seeding ladder** = optional enrichment for thin todos — see § Seeding ladder below. ¬ default; ¬ required to enter the spine.

Zoom-out on recon/investigate is **required** (template `cortex://notes/system/templates/recon-investigate-packet.md`). Cadence: sample every 5 spine closes or CHECKPOINT → consensus-steelman-posture § Cadence.

## Seeding ladder (optional enrichment — thin todos)

**Status: OPTIONAL.** Multi-step, multi-handoff (operator push on each web rung until web-anthropic handoffs are automated — distant future). Default path remains the spine with Grok densify. Use the ladder only when the operator opts in or the orchestrator explicitly chooses enrichment over cheap densify.

**Not:** a spine replacement · a standing Gate-2 requirement · an auto-fired path on every thin seed.

**Fire when (and only when opted in):** `judgment_required` ∧ thin/sparse seed ∧ material OPEN FORKS that need strategic frame before densify-close.

**Skip / do not offer by default:** `mechanical` / `trivial` · already dense · pure assertion/observation · operator wants speed over enrichment · no capacity for multi-handoff.

| Role | Job | Standing bind |
|---|---|---|
| `thin_seeder` | Scaffold todo / OPEN FORKS / corpus pointers; ¬ `implement_ready` | Grok 4.6 High (cursor) / Sonnet 5 (mobile) |
| `strategic_framer` | Gates / insertion / excludes; bind-shaped | Opus 4.8 High (web-anthropic) |
| `densify_adjudicator` | Close forks, distill attrs, may stamp densify-close | Opus 4.8 Max (web-anthropic) |
| rare escalate | Pre-codify / novel authority-class frame only | Fable 5.1 (credits/promo — never standing; B6 no-API-Fable-default) |

```
thin_seeder → strategic_framer → densify_adjudicator → [rare] Fable
  → GPT merged check → Composer
```

**Exit into spine:** after densify-close / `implement_ready`, enter at **GPT merged check → Composer**. ¬ recon restart; ¬ second densify; ¬ web mid-pipeline.

**Cost note:** each Opus/Fable rung is a manual web handoff today. Prefer spine+Grok densify unless enrichment value clearly beats handoff cost. Corpus-backed enrichment stage: deferred past v1.

Charter detail: `cortex://notes/system/threads/4830-workflow-doctrine-charter.md`. Routing SOT: consult-routing § Autonomous work-item spine.

## FOL pipeline (Gate 1–9)

### 1. Seed

Create the `todo:` entity. Charter = a **confirmed assertion** citing the source (`agent-bus:{thread}`), ¬ description prose — description is not FTS-graded or evidence-bound the way an assertion is. `∀ seed : search_existing_first` — building a weaker duplicate next to already-scoped work is wasted motion (hit twice in one session). Required seed fields + the two-tier contract → `rule:todo-lifecycle` § Two-Tier Todo Contract.

### 2. Triage

Set `attributes.density_triage ∈ {judgment_required, mechanical}` explicitly. `density_triage ∈ {unset, unknown} ⇒ implement_dispatch_blocked` — silent until checked. **Declared** by an authorized reasoner/operator, ¬ inferred by the stager (the tier that would need to escalate is the one that won't). → consult-routing § Densify lane; `rule:todo-lifecycle` § Staging-tier triage.

**`mechanical` is a triage label, not a dispatch `contract=` value** — do not carry it into `team_dispatch`. Gate 7's `contract=implement` is correct for `source_ref`-based cursor-sdk dispatch regardless of `density_triage`; `contract='pure-mechanical'` is a different, `packet_path`-only lane and rejects `source_ref` with a `validation_error` (friction 23525).

### 3. Recon + settlement (judgment_required)

Dispatch a cheap recon pass **before** engaging design — "candidate, re-derive don't elaborate," never resolves forks alone. `light_corpus ⇒ skip_permitted`, but skip **consciously**, ¬ silently.

**Zoom-out (binding):** touch-point inventory + bug-class/sibling grep + closeout `## Secondary findings` (or `None observed.`) with disposition verify-now / flag-deferred / spin-ticket. ¬ open-ended redesign. Copy template: `cortex://notes/system/templates/recon-investigate-packet.md`. Skeleton: `handoff-dispatchers.mdc` § Recon/investigate packet skeleton.

**Settlement:** bind forks blank-first. **`authority_fork` STOP** — fork touching {provider default model string ∨ `anthropic/` identity ∨ product/catalog identity ∨ external-counterparty artifact ∨ money-/risk-moving config ∨ irreversible deletion} ⇒ do-not-settle; tag `authority_fork`; escalate (consult-routing). Codified-class forks → covering `decision:*` only. Confirmed deadlock after re-investigate → web escalation terminal.

→ cheap-recon-before-escalation; consult-routing § Autonomous work-item spine; orchestrator-workflow R7/R8.

### 4. Densify (Gate 2)

Write the dense spec at `cortex://notes/system/specs/{slug}.md`: 8 sections (problem, non-goals, provenance, touch-points, bound design/forks, implementation, acceptance, verification) + a `<reasoning_trace>` with the literal attestation "no fork remains open," zero `OPEN:` markers. Start from `cortex(tool='doc_template', arguments='{"doc_type":"implement_dense_spec"}')` — it returns the `validate_dense_spec`-compatible skeleton (`libs/cortex_store/dispatch_ops/adapters/_doc_template.py`); hand-rolling headings from memory is the trap, because `validate_dense_spec` matches heading **text** by regex, not concept name — read `_SECTION_ACCEPTED_PATTERNS` in `libs/implement_admission/dense_spec_schema.py` first (e.g. "non-goals" must contain "non-goal" or "scope exclusion"). **Not yet auto-invoked at Gate-1 staging** — friction **21706** ("Gate-1→Gate-2 boilerplate wiring gap") is still open; call `doc_template` explicitly until that wiring lands. Distill `files_expected` + `acceptance_criteria` (+ `required_skills`) onto the todo afterward — the materializer reads **attributes only**, never spec prose. **`required_skills` MUST be registered `agent_skill:{slug}` entries** (committed skill source table); rule-only names like `testing-discipline` → `SkillSourceResolveError` at Gate-3 materialize. → handoff-packet-authoring; consult-routing § Densify lane; required-skills-pickup.

**Default densify seat (spine):** Grok densify under the spine orchestrator (web-Sonnet or cursor). Mid-pipeline web densify redo **forbid**. Seeding ladder (`densify_adjudicator` = Opus Max) is **opt-in only** — see § Seeding ladder; do not auto-escalate thin seeds into multi-handoff enrichment.

**Attended / non-spine override:** independent densify handoff or inline authorship only when the operator asks per-instance, or when not on the autonomous spine path. (Prior directive a21700 "always independent web-anthropic densify" is **superseded for spine-default code-lane** by `decision:autonomous-work-item-spine` / agent-bus:4830 — retained only as the attended-path preference.)

### 5. Panel review (material decisions only)

`contested_fork(changes_invariant ∨ hard_to_reverse ∨ close_options_w_real_reversal_cost) ⇒ 2-family panel` (skeptic=grok, reviewer=gpt-5.5) on the spec **before** dispatch, not after. Adjudicate yourself: steelman each option going in, accept/reject each falsifier, write the trail. → consensus-steelman-posture.

### 6. Check / skeptic ratification (gate distinct from Gate 2)

**Spine default check:** one merged GPT check (prefer gpt-5.6-terra/Sol; split only on residual/amend) after densify-close — the standing cross-family falsifier (`decision:autonomous-work-item-spine`). ¬ dual GPT-5.5; ¬ web as check substitute. Code-lane live-source / `workspaces://` ⇒ `seat=cursor-sdk` + `model=cursor/gpt-5.6-*`; API reviewer only when fully pre-staged (friction a24082). Densify-close `implement_ready` ≠ Gate-6 ratification; access-only REVISE ≠ Gate-6 close.

**Admission still requires** (when `judgment_required` and not waived): `status(todo, skeptic_ratified, current)` whose `evidence_uris` include (1) the **ratifying** check/skeptic thread — the thread whose turn carries a literal `FILE_EVIDENCE_PATHS:` block (bare paths — no bullets, no fence) — and (2) the dense spec's **exact** current `spec_sha256:<hex>` as a literal list member. Prefer `agent-bus:{id}#turn-{N}` pinning the FILE_EVIDENCE turn. The grounding parser (`select_agent_bus_evidence`) uses the **first** `agent-bus:` URI; citing an orchestration root / densify arc / WIP thread instead of the ratifying check thread → `skeptic_evidence_missing` / `stamp_missing`. Thread-only citation without the hash → `422 skeptic_pass_missing` with no hint (friction 21656). The GPT merged-check turn is the usual FILE_EVIDENCE home on spine arcs.

Multi-round is expected: a round-1 `REJECT` naming a decisive falsifier is the gate working — revise the spec and re-ratify against a **fresh** `spec_sha256`, ¬ route around it. A `RATIFY-WITH-CONDITIONS` verdict may be applied in refined (not verbatim) form only when the deviation is documented and the reviewer's own falsifier is re-verified under it → consensus-steelman-posture § Ratification loop + conditions.

**Implement proceed (spine default):** after densify-close + GPT merged check pass + attrs distilled, proceed to Gate 7 implement **without** a per-item operator prompt — that is the autonomous spine. (Prior directive a21714 "always prompt before implement" is **superseded for spine-default code-lane** by `decision:autonomous-work-item-spine` / agent-bus:4830.)

**Attended / non-spine:** still prompt the operator before implement dispatch when not on the autonomous spine path, or when the operator has opted out for that todo.

### 7. Implement (Gate 3)

Thread-consolidation preflight first: pre-stage a pending+empty shell thread, verify (`bus_lifecycle_state=pending`, `turn_count=0`), THEN `team_dispatch(op=generate, seat=cursor-sdk, contract=implement, source_ref=todo:{slug}, dispatch_thread_id=<shell>)`. On spine default this fires after Gate 6 check pass; on attended/non-spine paths, after the operator prompt.

`contract=implement` is correct here regardless of `density_triage` — a `mechanical`-triaged todo does NOT mean `contract='pure-mechanical'`; that contract is `packet_path`-only and rejects `source_ref` (friction 23525; see § 2 Triage).

### 8. Verify

`¬trust(worker_self_report)`. Independently read the named deliverables off disk; run `quality_gate` directly. `closeout_turn = FAILED ⇏ work_failed` (see oversized-closeout gotcha). Verify **new test** deliverables with `ls`/Read on the `files_expected` paths, ¬ `git status` — `**/test_*.py` is gitignored repo-wide, so new tests never appear in `git status`/`git diff --stat` though they exist and pass (friction 21661). Record a `VERIFIED COMPLETE` assertion citing concrete tool-response data (quality_gate result, file contents), ¬ narrative.

### 9. Ship

Code is **not live until an explicit service restart** — restart is operator-gated, never assumed. Identify which service loads the changed code before asking, ¬ reuse whichever was restarted last. Close the todo via the atomic todo-close pipeline → `rule:todo-lifecycle` § Completion (+ its session-close reconciliation gate).

## Known gotchas (durable)

- **`CURSOR_SDK_CLOSEOUT` delivery failure** — a closeout body over ~8000 chars fails bus delivery and the turn reads `FAILED`; the underlying work frequently succeeded. Disk-verify before concluding failure (friction 21614, 21626 — fix not landed).
- **`skeptic_evidence_missing` 422** (Gate 6) — reply on the **ratifying** check/skeptic thread with a `FILE_EVIDENCE_PATHS:` footer (not the orchestration root), making that turn the cited `agent-bus:` evidence (prefer `#turn-N`); also bind exact current `spec_sha256:<hex>` in `evidence_uris` (precedent: assertions 21496/21497).
- **`implement_ready_triage_unknown`** — `density_triage` unset blocks everything downstream silently; check Gate 2.
- **`SkillSourceResolveError` at materialize** — a `required_skills` slug that is a rule/`.mdc` name (or otherwise absent from the skill source table) blocks Gate-3; distill only registered `agent_skill:` slugs.

## Live frictions (time-boxed — prune each when its fix lands)

`friction(f) resolved_structurally ⇒ delete bullet(f)`. Each is also a `friction()` on its owning skill (the real fix home); this list exists only to stop the same trip twice before that lands — ¬ a permanent home, ¬ a place to let gotchas accumulate. Frictions 21656/21659/21661 appear **both** here (canonical prune-tracker) and inline at their point-of-use gate (4/6/8): `¬ merge` — collapsing them into the gate prose is exactly the over-compression that reopened thread 4034. Each id stays an independently prunable bullet.

- First `cortex(tool="assert")` call: pass `derivation_type` explicitly or `422 assertion_quality_rejected` (21655).
- **Bind the dense spec's exact current `spec_sha256:<hex>` into the `skeptic_ratified` assertion's `evidence_uris` at write time** — and cite the **ratifying** check thread (`agent-bus:…#turn-N` with `FILE_EVIDENCE_PATHS:`), ¬ the orchestration root; `find_skeptic_assertion` requires the hash as a literal member or admission 422s `skeptic_pass_missing` with no hint (21656).
- Revise an assertion via the dedicated `supersede` op, **never** `assert(supersedes_id=…)` — without `force=true` it silently no-ops into a deduped return on similar claim text, discarding your new fields with no error (21657).
- After any `supersede` on an assertion carrying `predicate_form`, immediately `assertion_update(predicate_form=…)` — `supersede` drops it, silently breaking gates keyed on it (`skeptic_ratified`, `implement_ready`) (21658).
- Before writing dense-spec section headings, read `_SECTION_ACCEPTED_PATTERNS` in `libs/implement_admission/dense_spec_schema.py` first — `validate_dense_spec` matches heading *text* by regex, not the section's conceptual name (21659).
- Before any mutating `fs(…)` on an existing file, read the `fs` schema first — `write` is a full replace; for partial edits use `append`/`prepend`/`insert_at_line`/`replace` or the section-addressed `md_append`/`md_insert`/`md_replace` (21660).
- Verify new test deliverables with `ls`/`Read` on the `files_expected` paths, not `git status` — `**/test_*.py` is gitignored repo-wide; new tests never appear in `git status`/`git diff --stat` though they exist and pass (21661).
- `agent-bus reply` CLI: pass `--to`, `--subject`, `--after-turn` on every call — the CLI is stricter than the MCP tool's looser description (21662).

- Dense-spec path gate is basename-keyed to `cortex://notes/system/specs/{slug}.md`: `normalize_dense_spec_path` (`libs/implement_admission/gate_distillation.py`) silently discards any `source_uri` whose basename ≠ `{slug}.md`, falls back to the slug stub, and emits `dense_spec_sections_missing` against a path you never supplied — so one differently-named unified spec covering several todos can never satisfy the gate for any of them. Workaround: materialize the validated spec **byte-identical** (same sha) at each todo's `cortex://notes/system/specs/{slug}.md` and repoint that todo's `source_uri` there; verify via `doc_validate` + `implement_ready_preflight` (21998).

## Grounding manifest

| Load-bearing rule | Source |
|---|---|
| Gate 1–9 order + gotchas | agent-bus:4034 redo (2026-07-01) + pre-rewrite workflow SOT (`notes/system/backups/skill-compression-20260701/sot/todo-lifecycle.md`) |
| Spine default for `judgment_required` code-lane | `decision:autonomous-work-item-spine` · agent-bus:4830/4837 · consult-routing § Autonomous work-item spine |
| Densify default = Grok under spine orchestrator; a21700 attended-only | agent-bus:4830 (supersedes a21700 for spine-default) |
| Implement after GPT check without per-item prompt on spine; a21714 attended-only | agent-bus:4830 (supersedes a21714 for spine-default) |
| `doc_template` not auto-wired Gate-1→2 | friction **21706** (still open) |
| `doc_template` returns live dense-spec skeleton | assertion **21761** + `libs/cortex_store/dispatch_ops/adapters/_doc_template.py` |
| Heading-text regex, not concept name | `_SECTION_ACCEPTED_PATTERNS`, `libs/implement_admission/dense_spec_schema.py` (friction 21659) |
| Skeptic/check `FILE_EVIDENCE_PATHS:` + `spec_sha256` in `evidence_uris` | friction 21656; `find_skeptic_assertion` admission path |
| `entity_get(intent=body)` returns full markdown | assertion **21632** (direct_observation) |
| Two-tier seed contract, grouping, completion, session-close gate | `rule:todo-lifecycle` → `docs/agent-guides/rules/todo-lifecycle.md` |
| Live frictions | frictions 21614/21626/21655/21657/21658/21660/21661/21662 |

**Corpus snapshot:** 2026-07-11 (spine align). `corpus_updates ⇒ re-ground`.

## Related skills

- **rule:todo-lifecycle** — entity schema, two-tier seed contract, grouping, dispatch attributes, completion pipeline, session-close reconciliation (reference layer; cross-linked, not restated here).
- **implement-todo** — single-todo pickup readiness + DIRECT/DISPATCH/COORDINATE routing.
- **consult-routing** — densify lane, staging-tier triage, **autonomous work-item spine** (default code-lane shape).
- **orchestrator-workflow** — recon R7/R8; Sonnet/cursor as spine orchestrator.
- **handoff-packet-authoring** — dense-spec / implement-packet authoring.
- **consensus-steelman-posture** — panel adjudication (Gate 5) + spine sample cadence (C4).
- **cheap-recon-before-escalation** — recon economics (Gate 3); zoom-out substance.
- **entity-lifecycle-discipline** — todo→plan threshold, `workflow_state` semantics.

## Minimal operating summary

1. **Seed** — `todo:` + charter assertion citing source; search existing first.
2. **Triage** — declare `density_triage`; unset ⇒ implement blocked. Mechanical ⇒ skip spine ceremony.
3. **Recon + settle** — zoom-out fields required; `authority_fork` STOP → escalate; else bind forks.
4. **Densify** — 8-section dense spec from `doc_template`; **default Grok under spine orchestrator**; distill attrs. Seeding ladder = **optional** multi-handoff enrichment (opt-in only).
5. **Panel** — 2-family panel on contested material forks, before dispatch; adjudicate yourself.
6. **Check** — one merged GPT check (spine default) + `skeptic_ratified` / `FILE_EVIDENCE_PATHS:` / `spec_sha256` as admission requires; then **proceed to implement** on spine (¬ per-item prompt).
7. **Implement** — shell-thread preflight, then `team_dispatch` implement.
8. **Verify** — disk-read deliverables + `quality_gate`; `FAILED` closeout ⇏ failed work; `VERIFIED COMPLETE` on concrete data.
9. **Ship** — operator-gated restart of the right service; close via todo-close pipeline.
