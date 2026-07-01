---
name: descriptor-authoring-discipline
description: Read before creating, editing, or drift-reviewing any MCP tool descriptor (a fol_descriptor in canonical.yaml or a tool-level docstring) or any tool-call guidance sitting beside a JSON schema. Covers the Tier-1 (call-time contract, kept in-band) vs Tier-2 (reference depth, relocated to a populated skill + pointer) split, the descriptor budget, EXTRACT-BEFORE-DROP, drop/keep tag lists, the per-tool skill-pointer convention, and runtime-ALIGN drift review.
skill_class: authoring-discipline
trigger_short: descriptor ∨ fol_descriptor ∨ tool docstring ∨ slim ∨ distill ∨ skill-pointer
trigger_match_terms: ["descriptor-authoring-discipline", "fol_descriptor", "descriptor", "tool docstring", "canonical.yaml", "slim", "distill", "tier-1", "tier-2", "skill pointer", "mandate_safety"]
canonical: workspaces://universal-llm-gateway/docs/agent-guides/skills/descriptor-authoring-discipline.md
---

# descriptor-authoring-discipline

**Trigger:** creating, editing, or drift-reviewing an MCP tool descriptor (`fol_descriptor` in `config/mcp/canonical.yaml`, or a tool-level description/docstring), or any tool-call guidance that sits alongside a JSON schema.

## Principle

A descriptor carries ONLY what the JSON schema cannot. The schema already encodes parameter names, types, requiredness, and structure — restating any of that is pure redundancy and the primary source of bloat. The descriptor's job is the **behavioral contract**: facts an agent needs at call-time that no schema can express.

## Two tiers — contract stays in-band, depth distills to skills

Every line of a descriptor is one of two tiers. Triage it.

- **Tier 1 — call-time contract (ALWAYS in-band, compact).** The behavioral facts an agent needs to call the tool *correctly on this call* and cannot defer: idempotency / side-effect shape, preconditions / ordering, conditional semantics, routing / when-to-use, cross-surface omissions, behavioral error conditions. Small by construction — a handful of clauses. The descriptor is the one channel guaranteed in-context whenever the tool is callable; a Tier-1 fact MUST NEVER be relocated to a skill on the assumption the agent will fetch it (skills are pull-not-push, unreliable at call-time).
- **Tier 2 — reference depth (relocates to a named skill).** Full op enumerations, examples, taxonomies, edge-case catalogues, provenance / history, response-shape projections. An agent consults this when *authoring or deciding*, not on every call. It moves to the skill the descriptor points at; the in-band residue is the Tier-1 contract plus a `See agent_skill:X` pointer.

**Descriptor budget.** A descriptor that runs to several KB has crammed Tier-2 depth into the call-time channel — the most expensive context there is, paid on *every* exposure of the tool. Distill it: keep Tier 1, relocate Tier 2, leave a pointer. For an over-budget descriptor, agents are EXPECTED to pull the pointed-to skill for depth — that reliance is the accepted cost of not paying multi-KB per call-time exposure.

**Precondition on relocation (hard):** the pointer must resolve — the named skill must actually hold the relocated depth *before* you drop it from the descriptor. Distilling Tier-2 out without populating the skill is silent loss at the surface that matters most. Tier 1 is never relocated; Tier 2 is relocated only into a populated skill.

**Pointer convention (per tool, required).** Every tool's in-band guidance — the tool-level docstring, and each per-op descriptor — names its relevant skill(s) with a `See agent_skill:X` pointer, so an agent always knows where the depth lives. The pointer is mandatory, not decorative: it is the in-band half of the split and the only thing that makes Tier-2 relocation safe. A tool whose depth spans several skills points to each. The natural home is the docstring (the in-band channel the agent already sees when the tool is callable). (Operator directive 2026-06-30: each tool should point to the relevant skill(s), in the docstring.)

## FOL symbols are NOT required (G6 retired)

The historical header invariant — every `fol_descriptor` must contain ≥1 of `∀ ∃ ⟹ ¬ ∈` — is **retired** (operator-approved 2026-06-30). A descriptor is valid when it is **non-empty**: a behavioral clause OR a `See agent_skill:*` pointer suffices. Never inject a meaningless symbol stub to satisfy a symbol count — that reintroduces the bloat this discipline removes. Symbols are welcome where they genuinely compress behavioral structure (`¬idempotent (duplicate id → 409)`), never as decoration.

## DROP — redundant, the schema already carries it

- Logic formulas that restate the signature, e.g. `∀ id ∈ str ∧ type ∈ EntityType: op(id,type) ⟹ created ∨ Conflict`. Duplicates `properties`/`required` — delete it, **but first run EXTRACT-BEFORE-DROP below**: a formula block routinely carries one load-bearing behavioral fragment among the redundant ones.
- Parameter name / type / requiredness restatements.
- **Structural safety/class tags implied by `mandate_safety` or the op family.** DROP this enumerated set: `Read-only`, `Mutating`, `Write.`, `¬side-effects`, and `¬writes` **when it merely restates `mandate_safety: read_only`**.
- **Schema-overlap test (structure only):** prose that restates STRUCTURE — param names, types, requiredness, enums — duplicates the schema; DROP. **But a runtime BEHAVIOR (a default value, a normalization step, an ordering, a precondition) that merely happens to also appear in a `description` string is NOT structure** — it is Tier-1 contract. EXTRACT/KEEP beats schema-overlap-DROP whenever the fragment is behavior. (The schema `description` is itself unenforced prose that can drift; do not treat it as authoritative coverage of a behavior.)

## EXTRACT-BEFORE-DROP — a dropped formula may be the ONLY home for a behavior

A formula block is **not uniformly redundant.** Before deleting one, SCAN it for any Tier-1 fragment that lives nowhere else and re-home it as a behavioral clause. **Silent loss is the failure mode this step exists to prevent.**

**Extract toward RUNTIME, not the formula's wording.** The named fragments below are CANDIDATES to verify, never text to copy — a formula may itself be drifted. Run ALIGN and write the runtime truth:
- `journal_read` — default limit 3; ordering is **`id DESC` (insertion order)** — NOT the formula's stale "timestamp DESC".
- `entities_by_content_hash` — strips a `sha256:` prefix before lookup; default limit 5; dedicated duplicate-detection lookup.
- `session_handoff_upsert` — precondition: session must be CLOSED; mirrors the handoff to the transcript entity; upsert replaces prior; boot omits handoffs.
- `search` — **hybrid FTS5 + vector, CombMAX fusion** (returns `search_mode`) — NOT the formula's pure-"FTS5 match".
- `doc_validate` — delegates to `preflight_implement_ready`; skeptic grounding affects *status* (`skeptic_hash_missing`); on pass emits attestation tokens (`doc_validate:pass`, `spec_sha256`, `skill_digest`) — there is no single "skeptic attestation token".

**Rule:** deleting a formula requires a positive check that every behavioral fragment in it is either (a) already stated in prose, (b) re-homed as runtime-aligned prose now, or (c) genuinely schema-expressible structure. Only then delete.

## KEEP — behavioral, compact, in-band, schema can't express

- **Idempotency & side-effect shape:** `¬idempotent (duplicate id → 409)`, `upsert replaces prior`, `each call mints a new row`.
- **Preconditions & ordering:** `run X before Y`, `requires PASS attestation`.
- **Conditional semantics:** `transcript required iff depth=verbatim` — state the condition; never collapse a conditional into an unconditional. (FOL-shaped conditional clauses like `∀ intent ∈ {full,card}: response shape differs` are KEEP — conditional semantics, not signature restatement.)
- **Routing / when-to-use:** `preferred for session-local observations`.
- **Cross-surface omissions:** `boot omits handoffs — explicit retrieval only`.
- **Behavioral error conditions:** status codes tied to runtime behavior (not schema validation).
- **KEEP behavioral negatives** (do NOT confuse with DROP-able safety tags): `¬idempotent`, `¬creates`, `¬distillation`, `¬restamping`. These state side-effect SHAPE, which the schema cannot express. The test: does the negative describe *what the op does to state* (KEEP) or merely *its read/write class* already in `mandate_safety` (DROP)?

One fact per clause, no prose padding. Append `See agent_skill:X` for depth; the contract stays in-band.

## ALIGN to runtime — every KEEP claim

At write time and at every drift review:
1. Locate the runtime that implements the claim (handler, validation module).
2. Confirm it holds — **checkable claims first** (conditionals, required-field rules, default values, status codes, ordering, hop/limit caps). This is where dangerous drift hides; compact notation makes a one-word error easy to miss.
3. If the op's schema changed, delete descriptor text that now merely restates it.
4. Flag any claim you cannot ground in runtime rather than preserving it on faith.

**The failure mode this prevents:** `session_close`'s descriptor asserted an *unconditional* transcript requirement while the validator enforced it *only at depth=verbatim*. A preserve-verbatim migration would have faithfully shipped the bug. Drift review re-grounds the claim against source — it does not re-copy the text.

## Create-time checklist
1. Start from the schema; do not restate it.
2. Triage every line Tier 1 vs Tier 2; keep Tier 1 in-band, relocate Tier 2 into a populated skill + pointer.
3. Verify each behavioral claim against the handler as you write it.
4. Compact notation, one fact per clause. No symbol stub for its own sake.

## Drift-review checklist
1. For each behavioral claim, open the runtime that implements it.
2. Confirm match — conditionals, required fields, defaults, ordering, status codes, caps first.
3. For each formula you drop, run EXTRACT-BEFORE-DROP and record the scan.
4. Drop text that now only restates the schema or `mandate_safety`.
5. For an over-budget descriptor: confirm the pointed-to skill holds the Tier-2 depth before dropping it.
6. Flag unground-able claims.
