<!-- target:* -->
# Plan Slug Coherence

## Invariant

∀ plannable work — one canonical `{slug}` binds every artifact:

```
entity slug  ≡  spec basename  ≡  phase_dir name  ≡  plan slug  ≡  plan_phase prefix
```

Concretely, for `{slug} = manage-mvc-busy-channel`:

| Artifact | Value |
|---|---|
| Todo entity | `todo:manage-mvc-busy-channel` |
| Plan entity | `plan:manage-mvc-busy-channel` |
| Spec file | `tasks/specs/manage-mvc-busy-channel.md` |
| Phase dir | `tmp/prompts/manage-mvc-busy-channel/` |
| Phase entity | `plan_phase:manage-mvc-busy-channel/phase-N` |

## Derivation, not interrogation

**Invariant**: tools DERIVE `{slug}` from the entity id (the part after
`plan:`/`todo:`) or the spec basename. They do ¬ ask the user for the phase-dir
name, plan name, or output path.

`entity_get(arg) → source_uri → spec → phase_dir = tmp/prompts/{slug}/`. A
mismatch at any link spawns a divergent `plan:` entity — the single most common
plan-authoring friction. Bind once; reuse everywhere.

Multi-todo deliverable arcs use a `project:` entity + spec rather than a master
todo entity.
<!-- /target:* -->
