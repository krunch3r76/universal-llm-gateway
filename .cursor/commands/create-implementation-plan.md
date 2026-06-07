No-BC refactor. Breaking changes allowed.

**Load**: `@patterns_ws` `@routing_ws` `@modularization` `@core_ws` `@services_ws` `@topology_ws` `@event-debugging_ws` `@plan-slug-coherence_ws`

## Argument Forms

```
/create-implementation-plan plan:{slug}        # resolve entity → source_uri spec → author phase docs
/create-implementation-plan todo:{slug}        # same, via the todo's spec (source_uri)
/create-implementation-plan tasks/specs/x.md   # spec path directly → author phase docs
/create-implementation-plan <free text>        # current behavior: synthesize plan from prose
```

### Entity-Resolution Rule (deterministic)

For `plan:{slug}` and `todo:{slug}` args:

1. `cortex(tool="entity_get", arguments='{"entity_id":"{arg}"}')`.
2. Follow the entity's `source_uri` — that spec **is** the plan source. Read it
   (and any `phase_dir` / `phases_planned` attributes that scope the work).
3. Author phase docs into `tmp/prompts/{slug}/` where `{slug}` ≡ the entity id
   after the `plan:`/`todo:` prefix. ¬ ask the user for the directory name —
   DERIVE it from the entity id.

This auto-aligns the output dir with the plan entity: per
`@plan-slug-coherence_ws`, entity slug ≡ spec basename ≡ phase_dir name ≡ plan
slug ≡ `plan_phase` prefix. A mismatch spawns a divergent `plan:` entity — the
derivation rule prevents it.

For `tasks/specs/x.md` args: `{slug}` ≡ the spec basename (minus `.md`); same
derivation for the phase dir. For `<free text>`: synthesize a slug, then the
same coherence invariant binds spec basename / phase_dir / plan id to it.

**Worked example**: `/create-implementation-plan plan:manage-mvc-busy-channel`
→ `entity_get` → `source_uri: tasks/specs/manage-mvc-busy-channel.md` → author
phase docs into `tmp/prompts/manage-mvc-busy-channel/`.

**Phased-spec lift**: if the resolved spec carries a `## Phases` section
(authored via the phased `tasks/specs/_TEMPLATE.md` / `/draft-spec`), each phase
section's durable header (`Expected Executor`, `Executor Mode`, `Parallel-group`,
`Depends-on`, `Expected Files`, `Verification`) lifts 1:1 into a `phase-N.md`
header. The remaining work is filling the Task code blocks per the Code Block
Completeness rules below — a near-mechanical lift, not a from-scratch decomposition.

## Workspace Extensions

### Library-First Pre-Plan Sweep (MANDATORY)

`∀ plan that creates new infrastructure ⇒ author.preceded_by(sweep(libs/, adjacent_primitives))`

Per `cortex:agent-skills/architecture-invariants.md` §Library-First Discovery (`[universal:libs-first]`) and `cortex:agent-skills/implementation-plan-workflow.md` §Plan-author-side pre-flight (Pass 1).

Before drafting phase docs, sweep `libs/` for primitives whose contract overlaps or is adjacent to the planned work. Adjacent primitives constrain design choice and exception vocabulary even when the exact use case differs.

Two questions per planned new primitive P:

1. Is there a `libs/` primitive that already does P, or close to it? → use it, or extract a sibling (e.g. `libs/sse/passthrough.py` next to `libs/sse/accumulator.py`).
2. If no full match, does a `libs/` primitive carry the exception types, observability hooks, or timeout/cancellation semantics P will need? → reuse the taxonomy in phase docs; ¬ let executors invent parallel error codes.

Surface findings in README "Review findings" table. Architectural forks the plan cannot resolve unilaterally (e.g. "use existing primitive vs extract sibling primitive") go in Open questions with the design fork named — the executor MUST resolve before implementing.

Curated libs landscape for ULG plans: `cortex:agent-skills/ulg-architecture.md` §Libs Inventory. Refresh from source via `list` on `libs/` to catch packages added since the inventory was last updated.

**Anti-pattern (plan:pipeline-terminal-passthrough-streaming Phase 2, 2026-05-27):** Plan called for ad-hoc `ProxyClientError` codes (`upstream_non_streaming`, `empty_stream`) for SSE streaming failures without sweeping `libs/sse/`. The libs already carried `accumulate_sse_stream`, `SSEReducer[State]`, and the `SSE*Error` exception hierarchy. Accumulator shape didn't fit passthrough use case, but exception taxonomy, stall-timeout semantics, and cancel-check pattern were directly applicable. Operator caught mid-handoff; Phase 2's design fork now includes "extract libs/sse/passthrough.py" as a legitimate option. 5 minutes of upfront sweep would have closed it in the deck.

### Executor Model Selection (per phase)

| Phase Type | Characteristics | Recommended Model |
|---|---|---|
| **Sparse/architectural** | Ambiguous scope, design decisions, trade-off analysis, few concrete file changes | Opus 4.8 Low thinking-on |
| **Detailed with pseudocode** | Clear specs, enumerated file changes, pseudocode/signatures provided | Sonnet 4.6 Medium thinking-on |
| **Mechanical** | Rename-and-move, pattern application, boilerplate, delete dead code | Grok 4.20 / Sonnet Low thinking-off |
| **Exploration/investigation** | Unknown territory, need to read and understand before acting | Sonnet 4.6 High thinking-on |

**Density Signals** — Sparse (needs reasoning): "Design a mechanism...", goals without file-level changes, cross-subsystem implications not enumerated. Dense (can use coding model): exact file paths, function signatures, pseudocode, concrete checklist.

∀ phase doc: state the density classification and recommended executor model. Split mixed phases or default to reasoning model.

**Proactive model switch**: ∀ phase transition — suggest user switch when planning is complete and execution begins, or when sparse phase produced dense specs. "These phases are [density]. Consider switching to [model] for execution."

**Evidence**: CursorBench 2026-03 (`tasks/discoveries/cursorbench-model-intelligence-rankings.md`).

### Sole Maintainer Constraint

**Invariant**: ∀ plans: ¬backward_compatibility unless explicitly justified and user-approved. Break loudly, update all consumers together. If BC seems needed for a specific case, prompt the user before including it.

### Event-Driven Design Check (MANDATORY for behavior changes)

- [ ] State from events (¬manual) | fire-and-forget (`publish_nowait`)
- [ ] New/changed behavior → event vocabulary updated (new signals, updated payloads)
- [ ] ∀ state transitions, decision points, failure modes: observable via events

∀ plans touching behavior: include in each phase:
```markdown
## Event Vocabulary
| Behavioral change | Signal | Action |
|---|---|---|
| {new flow / state transition} | `{signal.name}` | add / modify / none needed |
```

### Documentation Impact

Plans touching subsystem behavior:
- [ ] `docs/architecture/` synced (use `docs/architecture/README_AI.md` index; module `README_AI.md` for non-consolidated modules)

## Core
**Sole maintainer** ⟹ breaking changes improve codebase.
- ¬backward compat | ¬shims | ¬fallbacks | ¬deprecation warnings
- Clean architecture | migrate ∀ consumers immediately

## Agent Instructions
1. **Structure**: `./tmp/prompts/{name}/` (summaries → `./tmp/prompts/{name}/summaries/`)
2. **Template**: see Plan Template below
3. **FOL**: ∀ ∃ ∈ ⊆ ∪ ∩ ∖ ∅ ⟹ ⟺ ∧ ∨ ¬
4. **Model-aware**: Require `Expected Executor`, `Executor Mode`, optional `Consultation`
5. **Arch preference**: atomic ops > events > queues > sequential > locks
6. **Type hints**: ∀ functions: typed params + return
7. **Dependency analysis (MANDATORY)**: Before writing any phase doc, map all inter-phase dependencies explicitly:
   - Which phases modify files that later phases read or also modify? → `Depends-on`
   - Which phases produce logical outputs (new types, refactored interfaces, deleted symbols) that later phases consume? → `Depends-on`
   - Phases with disjoint `Expected Files` and no logical dependency → same `Parallel-group` letter
   - Every phase MUST declare `Parallel-group` and `Depends-on` — even `None` is a declaration (not an omission). ¬ leave blank.
   - Phases with `Depends-on: Phase N` may assume `summaries/phase-N-summary.md` is available as input — coordinator guarantees delivery before dispatch.

## Design Guidance

### Pattern Identification (MANDATORY)
- [ ] Label known patterns: `# Pattern: {Strategy|Factory|Observer|Decorator|...}`
- [ ] State explicitly: "No standard pattern" when custom solution required

### Alternatives (architectural decisions only)
```
- Candidate A: {approach} — tradeoffs: {+pros / -cons}
- Candidate B: {approach} — tradeoffs: {+pros / -cons}
- **Selected**: {choice} — rationale: {why idiomatic ∧ cohesive ∧ concise}
```

### Selection Criteria (ordered priority)
1. **Idiomatic**: stdlib > third-party | language-native patterns | established conventions
2. **Cohesive**: single responsibility | minimal coupling | clear boundaries
3. **Concise**: fewer moving parts | less ceremony | no unnecessary abstraction

## Design Checks

### SRP (MANDATORY)
- [ ] ∀ functions: ≤1 responsibility
- [ ] Handler >80 SLOC → split
- [ ] Module ≥3 responsibilities → directory split

## Code Block Completeness (CRITICAL)

∀ task in phase file: a lower-tier model must be able to execute
it with ZERO additional reasoning or source-file reading.

- **`modify` tasks**: show complete BEFORE block (current code, with 3-5 lines
  of surrounding context) AND complete AFTER block (full replacement).
- **`create` tasks**: show the complete file — every import, every function.
- ¬ partial snippets with `# ... rest unchanged` or `# existing code`
- ¬ prose-described code ("add a method that does X") — write the code.

## Plan Template

This Plan Template is the single source of truth for the **full** phase-doc shape.
`tasks/specs/_TEMPLATE.md` reproduces only its durable header subset (the planning
fields), by pointer — it does not duplicate the Task code-block completeness rules.

```markdown
# Phase N: {Feature}

**Expected Executor**: {model}
**Executor Mode**: {thinking | non-thinking}
**Parallel-group**: {letter A|B|C|... — phases sharing a letter dispatch simultaneously} or None
**Depends-on**: {Phase N, Phase M — wait for these summaries before dispatching} or None
**Optional Consultation**: {model: question (success: criteria)} or None
**Suggested Reviewer**: {model} or none

## Prior Phase Inputs
(Only if Depends-on is set) List what this phase assumes is available from prior summaries.
Example: "Phase 1 summary — new `FooClient` interface in place; old `BarService` deleted."
Omit section entirely if Depends-on: None.

## Objective
{1-2 sentences}

## Tasks
### Task 1: {Name}
**Pattern**: {Strategy|Factory|Observer|...} or None (custom)
{One sentence: what changes and why.}

## Verification
- [ ] Compile: `python -m compileall -q {modules}/`
- [ ] Import: verify all imports resolve
- [ ] Lint: `ruff check {files}`
- [ ] SRP: handlers ≤80 SLOC | functions <3 responsibilities
- [ ] ¬compat layers | obsolete deleted | ∀ consumers updated

## Expected Files
Create: {paths} | Modify: {paths} | Delete: {paths}

## Cleanup
- Obsolete removed | Unused imports eliminated | Dead code deleted
```

## Policies
**Aggressive Cleanup**: DELETE immediately | PURGE unused | MIGRATE aggressively | REMOVE compat | BREAK APIs
**No BC**: ¬backward compat | Clean breaks > gradual | Delete old when new | Update ∀ consumers
