Manage the project TODO list conversationally.

**Workspace**: Load `@todo_ws.mdc`.

## What This Is

Todos are **Cortex entities** (`type: "todo"`) in `~/.cortex/cortex.db`, not a
flat file. Each has `id: todo:{slug}`, a typed `workflow_state`
(`open|in_progress|blocked|done|deferred|cancelled`), and
`attributes: {priority, domain, required_skills?, backlog?, seed_contract_ack?}`.
Rich todos carry `source_uri: tasks/specs/{slug}.md` (RAG-indexed in the
`todo_specs` scope).

`tasks/todo.yaml` and `tasks/todo.archive.yaml` are **frozen pointer files** —
¬read, ¬write. They are retained for navigation only (per `todo_ws.mdc`).

**Grouping**: when ≥2 todos share a deliverable arc, group them under a `project:`
entity (spec + graph edges), or as a `plan:` of ordered phases for one todo — not
a master-`todo:`. A `todo:` is always a **leaf**. The phantom `task:` subsystem was
retired 2026-06-04. See `@awareness_ws.mdc` and `tasks/specs/agent-workflow-parity.md`.

## Instructions

### 1. Retrieve

For user intent ("what's open about X?", "next?"), prefer ranked retrieval:

```
cortex(tool="todo_candidates", arguments='{"query": "<intent>", "limit": 8}')
```

For a full open enumeration: `cortex(tool="entities", arguments='{"type":
"todo", "workflow_state": "open"}')`. For stale/lifecycle review:
`cortex(tool="todo_audit", arguments='{"stale_days": 30}')`.

### 2. Present clearly

Display items grouped by `priority` (high/short first), showing `id` (the
`todo:` slug), `name`, `domain`, and a one-sentence summary. Omit `done` items
unless asked. If `source_uri` is set, note the linked spec.

### 3. Handle the conversation

#### 3a. Pickup — execute an existing todo

Trigger: `/todo pickup {slug}` (Cursor) or `Pick up todo:{slug}` (any seat).

Load the `implement-todo` skill and run its protocol (SOT: `cortex:agent-skills/implement-todo.md` — verify live → **§1b load governing skills** → gauge readiness → route → act → close). Do not restate steps here.

#### 3b. Seed contract for implementation-intent todos (DEFAULT)

∀ todo created without an explicit backlog/deferred marker: treat it as
**implementation-intent** and apply the rich-seed contract atomically:

1. Create the entity with `id: todo:{slug}`, `type: todo`, `priority`, `domain`,
   `required_skills: [<slug>, ...]`. **Attach skills by domain**, not ad hoc:
   read the **domain → default `required_skills` table** in `@todo_ws.mdc` and
   start from the ULG pair floor (`architecture-invariants`, `ulg-architecture`)
   for any repo work, then add domain-specific skills (e.g. `build-pipeline`,
   `consult-routing`). Implementation-intent todos MUST carry ≥1 skill; this is
   what pickup loads before readiness (`implement-todo` §1b).
2. Write a stub spec at `tasks/specs/{slug}.md` (Problem / Scope / Acceptance
   only — no code blocks per `/draft-spec` discipline). Set `source_uri` to this path.
3. Wire context edges via `relationship_create`: `references → decision:*` (if a
   decision was made this session), `relates_to → service:*` / code entity (if known),
   `evidence_uris` pointing at any agent-bus thread sidecar.
4. Wire skill edges via `relationship_create` (type=`requires`): **one edge per
   `required_skills` entry** to the `agent_skill:{slug}` entity. The attribute and
   the edges are the two halves of the same fact (`implement-todo` §1b reads their
   union) — seed both so a graph traversal and an attribute read agree. At pickup,
   each slug resolves via `entity_get(agent_skill:{slug}).source_uri` — prefer
   `fs(workspaces, …)` when `workspaces://` (Track A git SOT under
   `docs/agent-guides/skills/`).

**Prose mentions in description do not count.** Only graph-traversable edges
satisfy the seed contract (verified by `todo_implementation_seed_incomplete` gate).

**Backlog exception**: if the user says "backlog only", "not yet", or provides
`workflow_state=deferred` intent — create a **simple todo** (entity only, no stub
spec) and set `attributes.backlog=true`. The gate will not fire on it.

**Documented-intent escape**: if a todo cannot carry the full contract for a
specific known reason, set `attributes.seed_contract_ack='<reason>'`.

| User intent | Action |
|---|---|
| `/todo pickup {slug}` / `Pick up todo:{slug}` | Load `implement-todo` skill; run its 5-step protocol (§3a). Delegates entirely to skill — no protocol copy here. |
| "add X" | Apply rich-seed contract (§3b) by default. Write stub spec + edges atomically. Backlog/deferred intent → simple entity + `backlog=true`. Slug per `@plan-slug-coherence_ws`. |
| "done with X" / "mark X done" | Close via `pipeline(op="run", pipeline_id="todo-close", messages=[{"role":"user","content":"close"}], options={"todo_id":"todo:{slug}", "summary":"..."})` — atomic closure assertion + `workflow_state=done`. **Required** for priority=high. |
| "remove X" | `cortex(tool="entity_update", ..., "workflow_state": "cancelled")` — ¬ hard-delete. |
| "reprioritize X" | `cortex(tool="entity_update", arguments='{"entity_id":"todo:{slug}","attributes":{"priority":"..."}}')` |
| "what should I work on next?" | `todo_candidates` ranked retrieval; recommend top item, explain rationale |
| "tell me more about X" | `entity_get` (include the spec at `source_uri` if set) |
| "link X to Y" | `cortex(tool="relationship_create", ...)` or `edge_create` (e.g. `relates_to`, `derived_from`) |

Match by `id` slug, `name` substring, or natural language. When ambiguous,
confirm before writing.

### 4. Verify the write

After any mutation, read back: `entity_get` (verify `workflow_state` /
`attributes` — confirm `required_skills` is present and the `requires` edges
landed) per provenance-discipline. For a richer check after a seed, run the
seed-contract gate **scoped** to the todo:
`cortex(tool="audit", arguments='{"subject": "todo:{slug}", "kinds": ["todo_implementation_seed_incomplete"]}')`
— a clean (empty) result confirms `source_uri`, `required_skills`, and a
context edge are all present. Note: the `audit` op accepts `subject`; the
similarly-named `todo_audit` op is the *global* stale-todo sweep and takes no
`subject` (it would silently ignore it and return an unrelated list). A `done`
flip with zero assertions emits a `cortex.todo.closure.gap` signal — surface
it, do not ignore.

## Rules

- The agent synthesizes intent — do not ask the user to fill in fields manually.
- `slug` must be a lowercase hyphenated, unique slug — canonical across spec /
  phase_dir / plan per `@plan-slug-coherence_ws`.
- Valid `workflow_state`: `open`, `in_progress`, `blocked`, `done`, `deferred`,
  `cancelled` (enum-validated against the `workflow_schemas` registry).
- Closure (preferred): `pipeline:todo-close`. Manual `entity_update` +
  completion assertion is the fallback only.
- ¬ write `tasks/todo.yaml` — it is frozen.
