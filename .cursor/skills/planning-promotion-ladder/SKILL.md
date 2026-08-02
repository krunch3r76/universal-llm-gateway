---
name: planning-promotion-ladder
description: "Router for architecture artifact to executed work — how a spec/decision becomes a todo, gets shaped, densified, and promoted through the promotion ladder."
trigger_match_terms: ["planning-promotion-ladder", "planning_promotion_ladder", "artifact", "todo", "decompose", "execute", "cortex-planning", "router", "skill", "mapping", "architecture", "implementation"]
---

# Planning Promotion Ladder

Router for the spine `architecture artifact → executed work`. This skill does not own thresholds, packet contracts, deck structure, task mechanics, or slug rules. Its load-bearing contribution is the provenance substrate-placement matrix: where each link lives (entity attr / assertion attr / edge / dispatch param).

## Read / skip

Read when:
- promoting `decision:`, `cortex://notes/system/specs/{slug}.md`, or `tmp/prompts/{slug}-implement-packet.md` into executable graph work;
- answering “I have a packet/decision/spec — how do I get it implemented?”;
- deciding where provenance attaches while seeding `todo:` / `plan:`;
- orienting to the intent→entity→shape→decompose→execute funnel.

Do not use this skill to choose entity type/threshold (`entity-lifecycle-discipline`), author packets (`architecture-handoff-protocol.mdc`), author plan decks (`implementation-plan-workflow`), pick up one todo (`implement-todo`), or police slug binding (`plan-slug-coherence`).

## Five-rung ladder

| Rung | Stage | Artifact/entity | Owner |
|---|---|---|---|
| 0 | Architecture artifact | `decision:{slug}` · `cortex://notes/system/specs/{slug}.md` · packet | decision adoption · `/draft-spec` · handoff protocol |
| 1 | Promote to entity | `todo:{slug}` (+ optional `plan:{slug}`) | `/plan-seed` · `/todo` rich-seed · packet admission `source_ref` |
| 2 | Shape execution | single-pickup · `task:{slug}` steps · `plan:{slug}` phases | `entity-lifecycle-discipline` thresholds |
| 3 | Decompose (plan path) | `plan_phase:{slug}/phase-N` + phase packet | `/create-implementation-plan` / `implementation-plan-workflow` |
| 4 | Execute | green-gated work | `/implement-plan` · `implement-todo` |

Funnel, not mandate: single bounded `todo:` stops at rung 1–2; only a plan arc traverses all rungs.

## Rung 1 front doors

| Front door | Produces | Use when |
|---|---|---|
| `/plan-seed {slug}` | spec + `todo:{slug}` + `plan:{slug}` + `derived_from` edge | plannable work; want atomic spec/entities/linkage |
| `/todo "add X"` rich-seed | `todo:{slug}` + stub spec + `required_skills` + context edges | single implementation leaf; promote later if it grows |
| packet admission `source_ref` | bound dispatch from existing packet/ref | six-block packet already exists |

Front doors land a `todo:` leaf (`plan:{slug} --derived_from--> todo:{slug}`). This is a front-door property, not graph-wide invariant: Phase-1 forward-planning may seed root `plan:` → `plan_phase:` children directly; child todos optional.

## Provenance placement matrix

Attach at rung 1. Do not invent ad-hoc fields. Field semantics remain owned by `entity-lifecycle-discipline`; this table says where the link lives.

| Link | Mechanism | Layer | Points at |
|---|---|---|---|
| spec | `source_uri` | entity attribute | `cortex://notes/system/specs/{slug}.md` |
| derivation | `derived_from` | edge | `plan:{slug} → todo:{slug}` |
| rationale | `references` | edge | `decision:{slug}` |
| substrate | `related_to` | edge; `role` names substrate | `service:*` / code entity |
| membership | `child_of` | edge | `todo:{leaf} → task:{arc}` |
| bus/file evidence | `evidence_uris` | assertion attribute | `agent-bus:{thread}` · `workspaces://…` · `cortex://…` |
| dispatch artifact | `artifact_uri` / `artifact_storage` | assertion attribute | archived run/turn artifact |
| skill binding | `requires` | edge mirroring `required_skills` | `agent_skill:{slug}` |
| admission ref | `source_ref` | dispatch-time param, not stored | `todo:` / `agent-bus:` / packet ref |

Corollaries:
- `step:` is not a source_ref prefix or entity; task steps live in spec Steps table / `depends_on`.
- Edge type is `related_to`, not `relates_to`; `role` names the relationship.
- Seed both `required_skills` attribute and `requires` edge.
- PROV-O analogy is loose: `derived_from ≈ prov:wasDerivedFrom`; `source_uri/evidence_uris` are source annotations; `artifact_uri ≈ prov:wasGeneratedBy` target. `references` and `related_to` are Cortex-native/citation-ish, not strict PROV. Missing PROV bundles are deferred.

## Rung 2 shape decision

Do not restate thresholds here; they churn. Defer to `entity-lifecycle-discipline`.

| Outcome | Trip condition | Then |
|---|---|---|
| single-pickup | threshold untripped | `implement-todo` executes |
| `task:` arc (steps) | Todo→Task threshold | `task-grouping-discipline` |
| `plan:` arc (phases) | Todo→Plan threshold (≥2-of-5) | `implementation-plan-workflow` |

The seam is `decision:implement-todo-vs-plan-arc-boundary`: `implement-todo` runs threshold test at step 2 and exits to plan workflow if tripped.

## Anti-patterns

| Bad | Good |
|---|---|
| Restate Todo→Plan thresholds here | Point at threshold owner |
| Front-door seed `plan:`/`task:` with no `todo:` leaf | Front doors land `todo:`; bare root plan only via Phase-1 forward planning |
| Treat `todo:` leaf under plan as graph-wide invariant | Front-door property only |
| Paste packet body into description | Attach by reference: `source_uri` + `evidence_uris` |
| Use `relates_to` | `related_to` + role |
| Call task ordering “phases” | task=steps; plan=phases |
| Author phase docs for single bounded todo | single-pickup stops at rung 1–2 |

## Companion owners

- `entity-lifecycle-discipline` — type taxonomy, thresholds, step-vs-phase vocabulary, provenance field semantics.
- `task-grouping-discipline` — `task:` container, `child_of`, step ordering.
- `implementation-plan-workflow` — plan deck, phase docs, coordinator/executor.
- `implement-todo` — single todo and task-leaf pickup.
- `architecture-handoff-protocol.mdc` — six-block packet.
- `plan-slug-coherence` — one slug binds spec ≡ entity ≡ phase_dir.
