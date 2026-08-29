---
trigger_match_terms: ["entity-lifecycle-discipline", "entity_lifecycle_discipline", "new", "substrate", "todo-vs-plan", "scope", "cortex-planning", "high", "authority", "defaulting", "leaf", "todo", "runbook"]
description: 'On new substrate entities or todo-vs-plan/runbook scope — granularity, task:/plan:/todo:/runbook: vocabulary, child_of wiring, steps vs phases.'
---

# Entity Lifecycle Discipline

**Version:** 1.7-compressed  
**Authority:** HIGH — scope taxonomy for Cortex work and durable execution/thread persistence. Read before defaulting to leaf `todo:` when work implies new substrate, pipeline primitives, multi-entity creation, promotion, or persistent API/pipeline run shape.

Companions: `cortex-entity-restructure` owns assertion migration/supersession; this skill owns forward scope choice. `entity-creation-discipline` owns typed-claim verification on `entity_create`.

## Trigger

Read before:
- creating `project:` / `plan:` / `plan_phase:` / `task:` / `todo:` / `runbook:`;
- choosing todo vs task vs plan vs project vs runbook vs execution-thread;
- designing persistent Cortex layers for API/pipeline runs;
- choosing step vs phase vocabulary;
- promoting todo→task/plan, task→project, plan→project, or expanding scope;
- drafting/reviewing a Phase 1 plan pass for new entity creation.

`migrate_assertions ∨ split_existing_entity ∨ retire_parent_for_children ⇒ also_read(cortex-entity-restructure)`.

## Core invariant

`entity_type = scope`, not status, priority, progress, or operator mood.

Truth layers:
1. **Canonical:** graph (`child_of`, relationships, assertions).
2. **Editorial:** root `description` TOC (vision, phases/children cited by ID).
3. **Derived:** `render_subgraph(root, hops=2)`.

`canonical ≠ editorial ⇒ canonical_wins ∧ update(description)`.

## Step vs phase

| Term | Belongs to | Mechanism | Entity type? |
|---|---|---|---|
| **Step** | `task:` arc | ordered leaf `todo:` members (`depends_on` and/or spec Steps table) | No |
| **Phase** | `plan:` arc | shipping unit `plan_phase:{slug}/phase-N` + `/implement-plan` | Yes |

Rules:
- `task:` has steps, never `plan_phase:` children.
- `plan:` has phases, one shipping unit per `plan_phase:`.
- `step ∉ source_ref grammar`; no `step:` prefix/entity.
- Bug workflow stages are **investigate/execute**, not Phase N.
- “Phase 1 plan pass” keeps its workflow meaning; it is not `plan_phase:` taxonomy.

## Taxonomy

### `project:X`
Long-horizon vision track. Contains multiple plans/tasks/todos. Rarely closes. Description cites plan/task/todo IDs inline.

### `plan:X`
Bounded multi-phase execution arc under a project. Has ordered `plan_phase:` children. Lifecycle `open → in_progress → done/cancelled`. Use lowercase hyphen slug. Description lists phases with IDs and deferred-work callouts.

### `plan_phase:<plan-slug>/phase-N`
One shipping unit inside a plan. Owns state/progress assertions. Lifecycle `open → in_progress → done`. Avoid sub-phases; re-cut plan at same level if needed.

### `task:X`
Bounded, phase-free arc of ≥2 independently closable leaf todos. Peer of `plan:` under a project; ordered by **steps**, not phases.

- Members: `todo:` leaves via `child_of(todo → task)`; optional `depends_on` sequences serial work.
- Umbrella project: `related_to(role=umbrella_project)`, not `child_of`, unless portfolio hierarchy is intentional.
- Lifecycle `open → in_progress → done/cancelled`; close manually after children close + closure assertion.
- `task:{slug} ≡ cortex://notes/system/specs/{slug}.md`; `task:` and `todo:` must not share slug.
- Retired: legacy task workflow/API; live: `task:` entity type. Seed via `entity_create` + `relationship_create(child_of)`. See `task-grouping-discipline`.

### `todo:X`
Leaf work item, single concern. Lifecycle `open → in_progress → blocked/done/deferred/cancelled`. Never owns child todos.

Mandatory at creation:
- `required_skills`: 1–3 governing `agent_skill` slugs, optional `#section`. Floor, not bibliography. Executor still trigger-matches adjacent skills.
- `requires` edge `todo → agent_skill:<slug>` when the skill has a Cortex mirror. Attribute is the gate floor; edge is graph-queryable discipline.

`todo` is **not** default for substrate/pipeline design. If work implies new entity types, assertion/compression contracts, session_close/folder-close semantics, or pipeline step kinds ⇒ use `plan:`/`project:` with child todos as implementation slices.

### `runbook:X`
Invoked Cortex command. Operator trigger → execute the body at `source_uri`. Not a skill (`agent_skill:` is metadata on a loaded method). Not a Cursor `/command` (no repo mirror). Not a standing matter (genus handle + journal).

Shape: `description` = trigger + one-line pointer; `source_uri` = `cortex://notes/runbooks/{slug}.md` or an existing capability card; ≥1 assertion with `evidence_uris` to the body; edges to touch-points. One handle — ¬ also mint `document:{slug}-runbook`.

Mint via `teach-once-routine-mint` when a taught path is invoked and fails the skill split-test. Body authoring: that skill § Author runbook body (skill-writing principles, ¬ SkillReducer pipeline). SOT: `decision:runbook-as-cortex-command`.

### Execution / dispatch thread pattern

For API/pipeline multi-turn runs with verbatim file artifacts and referential rolling state:
- Anchor: one durable entity per run (convention TBD: `execution:`, `thread:`, or `pipeline_execution:`).
- Per turn: archive verbatim to artifact URI; assert on anchor with `derivation_type=compression` or `agent_observation`, `evidence_uris` to artifact, optional `artifact_uri/storage`.
- Ordering: `continues` edges or monotonic `observed_at` + thread-scoped assertion list.
- Skills: reinjected in live prompt; do not hide only in artifact storage.

`persistent_run_layer ⇒ plan/project work`; expect new primitives (`archive_turn`, recall index, context anticipation, folder-close export). Do not collapse to one todo.

## Implementation-intent todo seed contract

`todo.workflow_state ∈ {open,in_progress} ∧ ¬backlog ⇒ context_stripped_executor_can_spec_from(entity_get + traversal)`.

`prose_mentions(decision/thread/service) ∧ ¬edge_or_evidence(todo, that_context) ⇒ orphaned`.

### Implement-authority boundary

For `judgment_required` todos:
- `implement_ready_preflight.admitted=true` ≠ implement authority.
- `implement_ready` assertion must be authored by a reasoning-tier seat at Gate-2 densify close, not by mechanical/recon seat that set `density_triage` or drafted spec.
- Before self-stamping `implement_ready`, authoring implement packet, or moving recon→implement: load `consult-routing` §Densify lane → Implement-authority boundary.
- A reasoning-tier seat that performed recon may author `implement_ready` only after explicit Gate-2 densify close.

### Minimum seed floor

Implementation-intent todo MUST carry at creation:
1. `priority` + `domain` attrs.
2. Non-empty `required_skills` attr. Seed gate does not validate slugs or require edge — but **Gate-3 materialize does**: every slug MUST be a registered `agent_skill:{slug}` (rule-only names → `SkillSourceResolveError`). Create `requires` edge when Cortex mirror exists.
3. `source_uri = cortex://notes/system/specs/{slug}.md`; stub sufficient (Problem/Objectives/Acceptance, no code).
4. ≥1 incident context edge whose other endpoint type is not `agent_skill`: e.g. `references → decision:*`, `related_to → service:*`, or evidence URI to thread sidecar. Incidence is direction-agnostic; lexicographic storage can place todo as target.

`plan:{slug} + derived_from` is conditional on promotion thresholds, not seed floor.

### Prose SOT

Problem / Scope / Acceptance live only in the `source_uri` stub. Entity `description` is a 1–2 line pointer. Do not duplicate acceptance in description or invent `mirror_anchor` / `acceptance` attrs.

### Enforcement

1. Creation default: rich `/todo` or equivalent same-pass writes.
2. Discipline: this section.
3. Gate: `todo_implementation_seed_incomplete` WARNING when missing `source_uri`, non-empty `required_skills`, or incident context edge. Suppress only with `workflow_state=deferred` OR `attributes.backlog=true` OR `attributes.seed_contract_ack="<reason>"`.

Web/API seats lack slash-command ergonomics; hand-populate seed on create and rely on gate as backstop.

## Promotion thresholds

Re-evaluate type when signals appear; thresholds are heuristics.

### Todo → Task
Single concern fans into ≥2 independently closable leaf todos sharing one phase-free deliverable arc. Create `task:` and re-parent leaves via `child_of`. Pick `plan:` instead when pieces are ordered shipping phases.

### Todo → Plan
Promote when ≥2:
- ≥3 confirmed assertions describe distinct sub-concerns;
- ≥2 related todos are semantic sub-items;
- natural independently deployable shipping phases;
- name contains conjunctions (`X + Y`, `redesign + implement + migrate`);
- spec has multiple sections addressable as deliverables.

### Plan → Project
Promote when ≥1:
- sibling plan appears for related distinct execution;
- scope expands into indefinite future arcs;
- multiple plans share vision/framing.

### Task → Project
Rare/operator-gated: bounded task becomes indefinite track or accumulates sub-arcs/plans. Retire/recast task; do not leave live duplicate.

### Todo → sub-todos
Forbidden. Fan-out ⇒ create container (`task:` or `plan:`). Narrow exception: trivial sibling follow-ups under same container (`deploy; smoke-test; verify`).

## Decision status × workflow_state coherence

Invariant: `decision.status` (evidence grade) must not contradict `decision.workflow_state` (lifecycle).

- Birth default: `provisional + proposed`.
- Already-adopted operator decision: create as `confirmed + accepted` explicitly.
- Adoption: advance both axes together: `provisional→confirmed ∧ proposed→accepted`.
- Incoherent: `confirmed + proposed` (claims adopted while pre-adoption); `provisional + accepted` (lifecycle adopted but unverified).
- Subsequent: `accepted → implemented/superseded/reverted`. `rejected/deferred` are not decision workflow enum values; rejected proposal retires via `status=deprecated` + terminal workflow state.

Layers: truthful default (`_PROVISIONAL_BIRTH_TYPES`) + discipline + `decision_workflow_state_incoherent` WARNING. Do not hide contradictory birth state with grace windows.

## Phase 1 plan pass

For forward-planning (no migration/retirement), post before writes and wait for explicit operator approval. Silence ≠ approval. Phase 2 deltas require re-approval.

Plan pass specifies:
1. Root entity: type, ID, name, description, status, workflow_state, attrs, parent.
2. Children: type, ID, name, description, `child_of`.
3. Cross-refs: `related_to` / `references` to existing decisions/specs/plans.
4. Spec document entity/source_uri + `elaborates` edge if applicable.
5. Deferred child todos with why-deferred notes.
6. Execution order: root → children → edges → assertions.

If promoting/retiring existing entity, add `cortex-entity-restructure` migration table and verify-then-promote discipline.

## Description-field discipline

Project/plan descriptions are TOCs:
- cite every child ID inline;
- phase enumeration matches `child_of` edges;
- add phase ⇒ update description and create entity same session;
- delete phase ⇒ update description and retire entity or `archives_to` link.

Verify with `render_subgraph(root, hops=2)`.

## Anti-patterns

Do not:
- create nested todos;
- create todo without `required_skills`;
- create implementation-intent todo whose context exists only in prose;
- create plan without parent project;
- list a phase in description without `plan_phase:` entity;
- use `related_to` for true parent/child membership, except task↔umbrella-project association;
- create root entity before deciding type;
- migrate assertions during forward planning without `cortex-entity-restructure`;
- promote todo to plan/task and leave original orphaned/live duplicate;
- skip Phase 1 plan pass for ≥2 entities or any promotion;
- encode workflow_state into type choice.

## Non-trigger

- Single unambiguous leaf todo with no promotion ambiguity.
- Editing assertions/descriptions only (`cortex-orientation`).
- Restructuring overloaded entity (`cortex-entity-restructure`).
- Simple workflow_state update (`entity_update`).

## Related

`planning-promotion-ladder` · `task-grouping-discipline` · `cortex-entity-restructure` · `cortex-orientation` · `entity-creation-discipline` · `document-lifecycle-tracking`
