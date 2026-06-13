# Consult Routing

**SOT:** `cortex://agent-skills/consult-routing.md` — full playbook (R1/R2/R3, executor tier,
dispatch shapes, implement-lane `source_ref`). Read via `fs(sandbox="cortex", op="read", path="agent-skills/consult-routing.md")`
before dispatching. Boot skill; briefing card emits the compact index.
Cursor-indexed entry: `.cursor/skills/consult-routing/SKILL.md`.

Do not duplicate the cortex playbook body here — load the SOT section you need:

| Topic | Cortex section |
|---|---|
| Executor tier (R1/R2/R3) | `§ Executor tier & handoff mechanics` → `§ Canonical routing policy` |
| Implement lane `source_ref` | `§ Implement lane — source_ref` |
| Densify lane Gate-2 close | `§ Densify lane — Gate-2 close & attribute distillation` |
| Lane → transport table | `.cursor/rules/todo_ws.mdc` §Dispatch metadata |
| Handoff dispatch shapes | `projects/.cursor/rules/handoff-dispatchers.mdc` |
| General execution (contract-based, no packet) | `§ General execution lane (contract-based — no packet)` |
| Provider affordances vs roles | `§ Provider affordances vs team_dispatch roles (vocabulary)` |

**Quick ref — implement dispatch** (default = `cursor-sdk` generate + `source_ref`, server materialization; handoff = operator-attended fallback; canonical detail in cortex SOT § Implement lane — source_ref):

```python
# DEFAULT — materialize-and-execute (auto Composer, no IDE pickup)
team_dispatch(op="generate", role="cursor-sdk", contract="implement",
              source_ref="todo:{slug}", dispatch_thread_id="{arc-id}")

# FALLBACK — operator-attended IDE
team_dispatch(op="handoff", role="cursor-implement",
              source_ref="todo:{slug}", subject="Implement {slug}")

# LEGACY / escape-hatch — hand-authored packet_path only when source is not yet
#   representable as todo:/plan:/plan_phase: attributes, materializer output is
#   known-insufficient, or for materialized-vs-hand-authored debug:
# team_dispatch(op="generate", role="cursor-sdk", contract="implement",
#               packet_path="tmp/reviews/{slug}-implement-packet.md",
#               dispatch_thread_id="{arc-id}")
```

Introduced `todo:unified-admission-handoff-source-ref` (2026-06-08); arc shipped
`task:unified-implement-admission` (2026-06-10).

**Quick ref — wrap-only materialization** (cursor-sdk generate, no Composer spawn):

```python
team_dispatch(op="generate", role="cursor-sdk", contract="wrap",
              source_ref="todo:{slug}")
# → HTTP 200: materialized=true, packet_path, implement_spec_hash, packet_sha256
# source_ref required; packet_path forbidden; dispatch_thread_id exempt
# Rejects gating-misleading knobs (density_triage, review_opt_out_reason_code, auto_review_child)
```

`contract=wrap` is an exceptional generate-only transport/materialization contract — not a peer of `light-bounded`/`pure-mechanical`/`implement` semantically, but accepted on the `contract` slot for surface minimality.

## Densify lane — Gate-2 close & attribute distillation

Closing `ready-for-Composer-implement` on a `judgment_required` todo requires **two** distinct schemas:

1. **Dense-spec markdown** — validated by `validate_dense_spec` on the prose at `tasks/specs/{slug}.md`.
2. **Entity attributes** — `files_expected`, `acceptance_criteria`, and `required_skills` on the todo row. The `source_ref=todo:{slug}` materializer reads **only these attributes**; it never content-reads the spec prose.

At Gate-2 close, the densifier MUST distill a structured projection of the dense spec onto the todo:

```python
cortex(tool="entity_update", arguments={
    "entity_id": "todo:{slug}",
    "attributes": {
        "files_expected": ["path/a.py", "docs/..."],
        "acceptance_criteria": ["AC one", "AC two"],
        # required_skills when applicable
    },
})
```

**Dispatch-time gate:** `require_implement_ready` rejects (422 `implement_attrs_unpopulated`) when `files_expected` is empty or `acceptance_criteria` is empty/defaulted — before materialization. The `mechanical` triage path is unaffected.

**Waiver:** set `attributes.attributes_distillation_waived='<reason>'` to suppress the advisory session-close detector when documented intent waives distillation. This does **not** bypass the dispatch-time hard gate.

**Write-time shape gate:** `validate_distilled_attributes` rejects malformed implement-lane values and deprecated aliases (`files_modified`, `acceptance`) at `entity_create`/`entity_update`.

Once both schemas pass with zero open forks, Gate-3 closeout hands off to direct
`source_ref` implement dispatch — wrap is not on the happy path.
