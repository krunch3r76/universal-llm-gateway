---
name: required-skills-pickup
description: "Load entity required_skills before first write or dispatch on todo/task/plan pickup or team_dispatch handoff — never implement skill-blind."
trigger_match_terms: ["required-skills-pickup", "required_skills_pickup", "required_skills", "pickup", "implement", "todo", "task seed", "packet", "handoff", "readiness", "gauge readiness"]
---

# Required-Skills Pickup

Seat-agnostic SOT. Cursor-specific discovery stubs may exist, but web and Cursor seats resolve this body.

## Invariant

`pickup(todo|task|plan for implementation) ∨ author(team_dispatch handoff|implement packet) ⇒ load(attributes.required_skills) before first_write_or_dispatch`.

Reading `required_skills` is a precondition to readiness judgment. Never stage, densify, or implement skill-blind. Empty/unset is not skip permission; apply Floor.

## Resolution

```python
cortex(tool="entity_get", arguments='{"entity_id":"todo:{slug}"}')  # or task:/plan:
```

For each `slug ∈ attributes.required_skills`:
1. Confirm the slug is a registered **`agent_skill:{slug}`** in `config/skills.yaml` (`get_skill_catalog()`). Rule-only / `*_ulg.mdc` stems (e.g. `skill-surface`, `testing-discipline`, `capability-dispatch`) are invalid — they are Cursor rules, not catalog skills. Write-time Gate-2 / `validate_distilled_attributes` 422s `required_skills_uncatalogued`; Gate-3 materialize still raises `SkillCatalogResolveError` / `SkillSourceResolveError` as last-ditch. **Do not** add rule stems to the skill catalog to paper this over.
2. `cortex(tool="entity_get", arguments='{"entity_id":"agent_skill:{slug}"}')` — metadata / `source_uri` (authoring paths only; not runtime body load).
3. **Use the** `{slug}` **skill** — canonical slug; seat self-fetches body. ¬ fs-read skill markdown (`agent-skills/` retired; friction 23128).

Seat affordances:
- All seats: native boot/index + description-gated stubs (`<available_skills>` on Cursor; boot manifest on web).

## Floor

ULG-repo todo with empty/unset `required_skills` ⇒ default-load
`architecture-invariants` + `ulg-architecture` + `docstring-quality`.

Seed-time `requires` edges mirror `required_skills` (one edge per slug). `project_required_skills_no_relationship` fires when attribute and edges diverge.

## Recon-exit → Gate 2 reload

`density_triage=judgment_required ∧ Composer/cheap_seat_authored(cortex://notes/system/specs/{slug}.md) ⇒ reload(handoff-packet-authoring ∧ consult-routing) before implement-readiness judgment`.

Recon exit is reasoning-tier Gate-2 densify close, not self-stamp. `implement_ready_preflight.admitted=true` = declared-state consistency only, not implement authority.

## Related

`handoff-packet-authoring` · `consult-routing` · `task-grouping-discipline` · `implement-todo` §1b · `todo_ws.mdc`.
