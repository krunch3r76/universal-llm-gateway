---
trigger_match_terms: ["task-grouping-discipline", "task_grouping_discipline", "todos", "forming", "one", "arc", "cortex-planning", "deliverable", "skill", "creating", "master-todo.", "covers"]
description: 'On ≥2 todos forming one deliverable arc — read before seeding a container. Covers task: vs todo: vs plan:, child_of membership, bounded-arc grouping, and the never-a-master-todo invariant.'
---

# Task grouping discipline

Trigger: `≥2 leaf todos forming one bounded deliverable arc ⇒ read before creating master-todo or bare leaf without container`.

## Scope

`task:` entity type is live and is the canonical grouping container for bounded multi-todo arcs. Retired: legacy task workflow plumbing only (`task-seed`, `task-close`, `task_candidates`, `GET /boot-tasks`). Do not cite retired pipelines/ops/commands. Use generic Cortex primitives.

## Decision gate

| Situation | Create |
|---|---|
| Single closable unit | `todo:` only |
| One todo with ordered execution steps | `todo:` + `plan:` + `plan_phase:` via plan seed |
| Phase-free arc of ≥2 related leaf todos | `task:` + child `todo:` members |
| Long-horizon umbrella over many arcs/plans | `project:`; tasks `related_to` it |

Invariant: `todo:` is a leaf closable unit; `¬master_todo_for_grouping`. Grouping belongs to `task:`.

`plan:` = phased implementation deck with per-phase docs / `Expected Executor` / `/create-implementation-plan`. `task:` = bounded arc of independent leaf todos with no formal phase deck. A task child may later spawn its own plan.

Ordering words:

| Arc | Word | Mechanism |
|---|---|---|
| `task:` | steps | leaf `todo:` order via spec Steps table and/or `depends_on`; not `plan_phase:` |
| `plan:` | phases | `plan_phase:{slug}/phase-N` deck + phase files + `/implement-plan` |

## Find live tasks

```python
cortex(tool="entities", arguments='{"type":"task","workflow_state":"open"}')
```

## Seed a task arc

1. Write spec first: `cortex://notes/system/specs/{slug}.md` (frame, close criteria, child list). Copy `_task-template.md` if present.
2. Create entity:

```python
cortex(tool="entity_create", arguments='{"id":"task:{slug}","type":"task","name":"{Title}","description":"{1-2 line arc summary; cite child todo IDs inline}","source_uri":"tasks/specs/{slug}.md","attributes":{"priority":"medium","domain":"{domain}"}}')
```

`task:{slug} ≡ cortex://notes/system/specs/{slug}.md`; no plan/phase_dir binding.

3. Associate umbrella project with `related_to` unless a true portfolio hierarchy needs `child_of`:

```python
cortex(tool="relationship_create", arguments='{"source_id":"task:{slug}","target_id":"project:{umbrella}","type_id":"related_to","role":"umbrella_project","agent":"{seat}","session_id":"{session}"}')
```

4. Verify:

```python
cortex(tool="entity_get", arguments='{"entity_id":"task:{slug}","intent":"card"}')
```

## Attach leaf todos

Task member edge orientation: `todo --child_of--> task` (`source_id=todo`, `target_id=task`, `type_id=child_of`). Tool params are `source_id` / `target_id` / `type_id`, not SQL-column aliases.

Phase-free leaf:
```python
cortex(tool="entity_create", arguments='{"id":"todo:{child}","type":"todo","name":"{Child Title}", ...seed fields...}')
cortex(tool="relationship_create", arguments='{"source_id":"todo:{child}","target_id":"task:{arc}","type_id":"child_of","agent":"{seat}","session_id":"{session}"}')
```

Leaf with ordered steps: use `/plan-seed {child}` or `pipeline:plan-seed` for `todo:{child}` + `plan:{child}` + `derived_from`, then manually add `child_of` to the task. `plan-seed` does not auto-parent to task.

Optional serial ordering: add sibling `depends_on` edges, later todo depends on earlier todo.

## Close a task arc

No `task-close` pipeline exists.

1. Close blocking children via `pipeline:todo-close` or `entity_update(todo, workflow_state=done)` + closure assertion.
2. `entity_update(task:{slug}, workflow_state=done)`.
3. Assert task closure with spec + child closures in `evidence_uris`.

Open children at task close are advisory smell; resolve or explicitly defer.

## Coherence

- Active task should have ≥1 `child_of` member. `task_no_children` detector is specified but not currently registered; manual discipline only.
- Generic gates apply: `entity_source_uri_missing`, `entity_empty_description`, `dangling_relationship_target`. `project_required_skills_no_relationship` does not fire on `task:`.
- Drift gate flags retired workflow tokens and pipeline/cortex-op citations; bare `task:{slug}` entity refs are safe.

## Live exemplars

- `task:panel-dispatch-redesign`: minimal arc — 2 child todos + `related_to project:universal-llm-gateway` + decision reference.
- `task:cortex-status-traits`: ordered arc — 4 child todos via `child_of`, sequenced by `depends_on`.

## Slug + RAG rules

- `task:{slug} ≡ cortex://notes/system/specs/{slug}.md` only.
- `task:{slug}` and `todo:{slug}` must not share slug.
- Task specs under `cortex://notes/system/specs/` are covered by existing `todo_specs` RAG scope.
