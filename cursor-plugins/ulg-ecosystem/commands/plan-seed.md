Seed a plannable item atomically: phased spec + `todo:` + `plan:` + `derived_from`.

**Workspace**: Load `@plan-slug-coherence_ws.mdc` `@todo_ws.mdc`.

## Argument Forms

```
/plan-seed {slug}                  # derive name from slug; draft spec interactively
/plan-seed {slug} "Display Name"   # explicit entity display name
```

`{slug}` MUST be lowercase-hyphenated and unique — canonical across spec /
phase_dir / `todo:` / `plan:` per `@plan-slug-coherence_ws`. ¬ ask the user for
the spec path, phase-dir, or entity ids — DERIVE them all from `{slug}`:

| Artifact | Value |
|---|---|
| Spec file | `cortex://notes/system/specs/{slug}.md` |
| Todo entity | `todo:{slug}` |
| Plan entity | `plan:{slug}` |
| Phase dir (later) | `tmp/prompts/{slug}/` |

## Procedure

### 1. Write the spec (agent-side filesystem)

Copy `cortex://notes/system/specs/_task-template.md` → `cortex://notes/system/specs/{slug}.md` and fill it with the
user's intent. Honor the phased-spec discipline (`/draft-spec`): the durable
spec carries the `## Phases` section header (Expected Executor / Executor Mode /
Parallel-group / Depends-on / Expected Files / Verification) — NOT the full code
blocks (those are authored later by `/create-implementation-plan`).

Synthesize `name`, `description`, `domain`, `priority` from the user's intent —
do not ask the user to fill fields manually.

### 2. Seed the cortex artifacts (atomic, pipeline-side)

```
pipeline(op="run", pipeline_id="plan-seed",
         messages=[{"role":"user","content":"seed"}],
         options={
           "slug": "{slug}",
           "name": "{display name}",
           "description": "{one-line description}",
           "domain": "{domain}",
           "priority": "{low|medium|high}",
           "agent": "cursor",
           "session_id": "{current session id}",
         })
```

The pipeline creates `todo:{slug}` + `plan:{slug}` (both with `source_uri:
tasks/specs/{slug}.md`) and the `plan:{slug} --derived_from--> todo:{slug}`
edge in one call, returning structured per-call results.

### 3. Verify the writes (provenance discipline)

The response `Large ... payload flagged / Stored as rs_...` is a caching
notice, NOT a failure. Verify durable effect regardless:

- Inspect the pipeline result `json`: `ok == true`, `errors == []`.
- Read back the entities: `cortex(tool="entity_get",
  arguments='{"entity_id":"plan:{slug}","include_edges":true}')` — confirm the
  entity exists and the `derived_from` edge to `todo:{slug}` is present.
- Confirm the spec file exists: `fs`/read `cortex://notes/system/specs/{slug}.md`.

Surface any per-step `errors` verbatim. A partial seed (e.g. todo created but
edge failed) is re-runnable — cortex-api unique constraints make
`entity_create` idempotent on the same id.

## After Seeding

Author phase docs with `/create-implementation-plan plan:{slug}` — a
near-mechanical lift when the spec carries a `## Phases` section. Phase-doc
authoring is intentionally a separate step (it needs the libs-first sweep and
the code-completeness lift, which benefit from a reasoning-tier model).

## Rules

- The agent synthesizes intent — do not ask the user to fill fields manually.
- ¬ write `tasks/todo.yaml` (frozen) — todos are cortex entities.
- Closure remains `pipeline:todo-close`; this command only *creates*.
- One canonical `{slug}` — a mismatch spawns a divergent `plan:` entity.
