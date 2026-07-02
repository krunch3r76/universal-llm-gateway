---
sot: cortex
---

# Consult Routing

Read via `fs(sandbox="cortex", op="read", path="agent-skills/consult-routing.md")`
before dispatching. Boot skill; briefing card emits the compact index.
Cursor-indexed entry: `.cursor/skills/consult-routing/SKILL.md`.

Load the SOT section you need:

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

**`subject` on generate:** accepted but **ignored** on `op=generate` (the result-thread subject is auto-derived); the response carries a `subject_ignored_on_generate` warning. Use `op=to_thread` to set a thread subject (friction 19803).

**Preflight — verify live (mandatory).** Before any `contract=implement` dispatch (or Gate-3 `source_ref` handoff), run `implement-todo` §1 verify-live: `entity_get(todo:{slug})`, confirm `workflow_state ∈ {open, in_progress}` (not `done`/`superseded`/stale — boot-card rows and bus turns go stale; the entity is canonical). Loading `cursor-sdk-instruction-standard` is necessary but not sufficient; it does not check liveness. **Re-versioned / previously-done todo:** `source_ref` can reuse a stale pinned `implement_ready_assertion_id` (silent — the inactive-assertion reject only fires on actual supersession), so refresh first (supersede old assertion → update `implement_ready_assertion_id` → re-distill attrs) or prefer `packet_path` until the pin is verified. Canonical detail: cortex SOT § Implement lane — source_ref.

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

**Implement-ready predicate shape.** The Gate-2-close readiness assertion MUST normalize to `status({todo_id}, implement_ready, current)`. Lead the claim with implement-ready intent; avoid "reopened (in_progress)"/"in_progress" phrasing (it normalizes to an `in_progress` predicate or `has_attribute` no-match and the readiness gate won't recognize it). Cite dense spec + `spec_sha256:<hex>` in `evidence_uris`; set `predicate_form` explicitly if the normalizer mis-targets.

Once both schemas pass with zero open forks, Gate-3 closeout hands off to direct
`source_ref` implement dispatch — wrap is not on the happy path.



**Three entity-level preconditions the materializer also enforces** (beyond the two schemas above) — full checklist, 422 codes, gate order, and the dense-spec section list live in `cursor-sdk-instruction-standard.md` § Materializer preconditions (SOT — do not re-derive here):

- `density_triage` (entity attr) must be `judgment_required` (full gate) or `mechanical` (bypass); every other value (incl. `cross_cutting` / `dispatch_surface` / `admission_path` / `trivial`) → 422 `implement_triage_unknown`.
- the todo **entity** `source_uri` (NOT `attributes.spec_path`) must point at `tasks/specs/{slug}.md` or `notes/system/specs/{slug}.md` → else 422 `implement_not_ready_no_dense_spec`.
- `validate_dense_spec` requires EIGHT headed sections (problem · non-goals · provenance · touch-points · forks · implementation · acceptance · verification) + a non-empty `<reasoning_trace>` containing `no fork remains open` + zero `OPEN:` markers → else 422 `implement_spec_not_dense`.

## Codified bug reports

**Entity scope vs dispatch lane:** Friction/todo subsumption (`do NOT open a standalone
fix arc`; fold under an existing `todo:`/`task:`) governs **entity scoping** — which
cortex row tracks the work. It does **not** waive the **dispatch lane**:
investigate-before-execute (`web-consult` default) still applies unless the operator
confirms mechanical-only or a dense implement spec with investigate-close attrs already
exists. A friction claim that names an interim remedy is input to investigate, not
authorization to skip investigate and self-execute. Full transport matrix and pass
zoom-out duty: `fs(cortex, agent-skills/consult-routing.md)` § Codified bug reports.
