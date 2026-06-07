Alias for `/create-implementation-plan todo:{slug}`.

**Load**: `@plan-slug-coherence_ws` `@core_ws`

## What This Does

Given a todo entity, reads its `source_uri` stub spec + `required_skills` +
edges, then authors dense phase docs at `tmp/prompts/{slug}/` — enough for a
lower-tier executor to implement without additional context.

This is the **operator-facing one-liner** for the spec-without-session handoff
invariant: a fresh agent should be able to run `/spec-todo {slug}` and produce
complete phase docs without reading any agent-bus thread, git history, or prior
session transcript.

## Usage

```
/spec-todo {slug}               # e.g. /spec-todo rag-entity-gated-indexing
/spec-todo todo:{slug}          # either form accepted
```

## Instructions

1. Normalize the argument to `todo:{slug}` (strip `todo:` prefix if already present,
   then re-add — `todo:rag-entity-gated-indexing` is the canonical form).

2. **Entity resolution** (per `/create-implementation-plan` entity-resolution rule):
   ```
   cortex(tool="entity_get", arguments='{"entity_id": "todo:{slug}", "include_edges": true}')
   ```
   Follow `source_uri` → read the stub spec (Problem / Scope / Acceptance).
   Traverse edges: `references → decision:*` for rationale, `relates_to → service:*`
   for substrate context, `evidence_uris → agent-bus:*` for thread context.
   Load `required_skills` from attributes.

3. **If source_uri is null or the stub spec is missing**: flag the gap to the user
   (`todo_implementation_seed_incomplete` gate would fire on this todo). Ask the user
   to confirm before synthesizing from description prose only — spec-from-prose is
   an impoverished path that defeats the handoff invariant.

4. **Delegate to `/create-implementation-plan`**: proceed identically to
   `/create-implementation-plan todo:{slug}` from step 3 onward — libs-first sweep,
   executor model selection per phase, dependency analysis, phase doc authoring.
   Phase output dir: `tmp/prompts/{slug}/`.

5. **Verify**: after authoring, confirm that the phase docs contain the key substrate
   details that the entity's edges supplied (decision rationale, service anchor,
   mirror path, acceptance lines). If the entity had no context edges, note the gap
   in the phase README so the executor knows what's missing.

## Relationship to /create-implementation-plan

`/spec-todo {slug}` ≡ `/create-implementation-plan todo:{slug}` with the
additional obligation to surface gaps (step 3) and verify edge-derived content
(step 5). Use either form — they are equivalent for a well-seeded todo.
