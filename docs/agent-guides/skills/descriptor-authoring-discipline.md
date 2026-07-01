---
name: descriptor-authoring-discipline
description: Read before creating, editing, or drift-reviewing any MCP tool descriptor (a fol_descriptor in canonical.yaml or a tool-level docstring) or any tool-call guidance sitting beside a JSON schema. Covers Tier-1 call-time contract vs Tier-2 reference depth, descriptor budget, EXTRACT-BEFORE-DROP, drop/keep tags, per-tool skill-pointer convention, and runtime-ALIGN drift review.
skill_class: authoring-discipline
trigger_short: descriptor ∨ fol_descriptor ∨ tool docstring ∨ slim ∨ distill ∨ skill-pointer
trigger_match_terms: ["descriptor-authoring-discipline", "fol_descriptor", "descriptor", "tool docstring", "canonical.yaml", "slim", "distill", "tier-1", "tier-2", "skill pointer", "mandate_safety"]
canonical: workspaces://universal-llm-gateway/docs/agent-guides/skills/descriptor-authoring-discipline.md
---

# Descriptor Authoring Discipline

## Trigger

Read before creating, editing, or drift-reviewing an MCP tool descriptor (`fol_descriptor` in `config/mcp/canonical.yaml`, tool-level docstring/description), or tool-call guidance beside a JSON schema.

## Principle

`descriptor = schema_gap_contract`, not parameter documentation.

The JSON schema already carries names, types, requiredness, enums, and structure. Descriptor text carries only runtime behavior an agent needs at call-time and the schema cannot enforce.

## Tier split

| Tier | Keep where | Content |
|---|---|---|
| Tier 1 — call-time contract | in-band descriptor, compact | idempotency/side-effect shape; preconditions/ordering; conditional semantics; routing/when-to-use; cross-surface omissions; behavioral error conditions |
| Tier 2 — reference depth | populated skill + pointer | full op enumerations; examples; taxonomies; edge-case catalogues; provenance/history; response-shape projections |

`Tier1 ⇒ never_relocate_to_skill`. Skills are pull-not-push and unreliable at call-time.

`Tier2_relocation ⇒ populated_skill_exists ∧ descriptor_contains("See agent_skill:X")`. Dropping depth before the skill holds it is silent loss.

Descriptor budget: several-KB descriptor ⇒ Tier-2 bloat in the most expensive channel. Distill to Tier 1 + pointer.

Pointer convention: every tool-level docstring and per-op descriptor names relevant depth skills with `See agent_skill:X`; multiple depth homes ⇒ list each.

## FOL symbols not required

Historical G6 (`fol_descriptor` must contain `∀ ∃ ⟹ ¬ ∈`) is retired. Valid descriptor = non-empty behavioral clause OR `See agent_skill:*` pointer. Use symbols only when they compress real behavior; never add symbol stubs.

## DROP: schema/mandate redundancy

Drop after EXTRACT-BEFORE-DROP:

- Formulas/prose restating signature, parameter names, types, requiredness, or enums.
- Structural safety tags implied by `mandate_safety` or op family: `Read-only`, `Mutating`, `Write.`, `¬side-effects`, `¬writes` when merely restating `mandate_safety: read_only`.

Schema-overlap test: structure duplicates schema ⇒ drop. Runtime behavior (default, normalization, ordering, precondition) stays Tier 1 even if a schema `description` also mentions it; schema descriptions drift and do not enforce behavior.

## EXTRACT-BEFORE-DROP

Before deleting a formula/block, scan for the only home of a Tier-1 behavior; re-home runtime truth as prose/FOL. Formula wording can be stale: ALIGN to implementation, not original text.

Known runtime-alignment candidates:

- `journal_read`: default limit 3; ordering is `id DESC` insertion order, not timestamp.
- `entities_by_content_hash`: strips `sha256:` prefix; default limit 5; dedicated duplicate lookup.
- `session_handoff_upsert`: session must be CLOSED; mirrors handoff to transcript entity; upsert replaces prior; boot omits handoffs.
- `search`: hybrid FTS5 + vector, CombMAX fusion, returns `search_mode`; not pure FTS5.
- `doc_validate`: delegates to `preflight_implement_ready`; skeptic grounding affects status (`skeptic_hash_missing`); pass emits `doc_validate:pass`, `spec_sha256`, `skill_digest`.

Deletion gate: every behavior fragment is already stated, re-homed with runtime truth, or genuinely schema-expressible.

## KEEP: behavioral Tier 1

Keep compact in-band clauses for:

- Idempotency/side-effect shape: `¬idempotent (duplicate id → 409)`, `upsert replaces prior`, `each call mints row`.
- Preconditions/ordering: `run X before Y`, `requires PASS attestation`.
- Conditional semantics: `transcript required iff depth=verbatim`; do not collapse conditionals.
- Routing/when-to-use.
- Cross-surface omissions: `boot omits handoffs — explicit retrieval only`.
- Runtime behavioral errors/statuses.
- Behavioral negatives: `¬idempotent`, `¬creates`, `¬distillation`, `¬restamping` when they describe state effects, not read/write class.

One fact per clause. Append pointer for depth.

## ALIGN every KEEP claim to runtime

At write time and drift review:

1. Locate handler/validator implementing the claim.
2. Check conditionals, required-field rules, defaults, status codes, ordering, caps first.
3. If schema now carries structure, delete descriptor restatement.
4. Flag ungrounded claims; do not preserve on faith.

Failure anchor: `session_close` descriptor once asserted unconditional transcript requirement while runtime required transcript only at `depth=verbatim`; preserve-verbatim migration would have shipped drift.

## Checklists

Create:

1. Start from schema; do not restate it.
2. Triage each line Tier 1/Tier 2.
3. Verify behavioral claims against runtime.
4. Keep Tier 1 compact; move Tier 2 only into populated skill + pointer.
5. No decorative symbols.

Drift-review:

1. Open runtime for each behavioral claim.
2. Confirm conditionals, required fields, defaults, ordering, status codes, caps.
3. For each dropped formula, run EXTRACT-BEFORE-DROP and record scan.
4. Drop schema/`mandate_safety` redundancy.
5. Confirm pointed-to skill holds Tier-2 depth before dropping depth.
6. Flag ungroundable claims.
