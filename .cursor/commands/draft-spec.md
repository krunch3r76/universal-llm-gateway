Draft a specification for work items going through consultation or multi-phase implementation.

## When to Use

∀ work items that will go through `/consult-architect`, `/consult-plan`, or multi-phase implementation: draft a spec first. The spec is the input to consultation and the source of truth for implementation phases.

## Template (MANDATORY)

∀ spec: author against `cortex://notes/system/specs/_task-template.md`. Copy it, fill every
mandatory section, and keep the section order. The template carries the
durable phase header that downstream tooling lifts 1:1 into phase docs — do
not drop the `## Phases` section.

## Structure

The template defines six narrative sections plus a phased section:

1. **Problem** (mandatory) — current state, quantified impact, unidentified needs
   (frame gaps as questions for consultation).
2. **Objectives** (mandatory, tiered) — Primary / Secondary / Stretch.
3. **Requirements** (mandatory, grouped) — data model / migration / integration /
   agent guidance / quality.
4. **Architecture** (recommended) — current vs target (ASCII / structured text).
5. **Key files** (recommended) — table mapping files to roles.
6. **Success criteria** (mandatory) — checkable, objective outcomes.

### Phases (MANDATORY for multi-phase work)

Emit one `### Phase N` section per planned phase. Each section's header carries
the **durable planning header** — `Expected Executor`, `Executor Mode`,
`Parallel-group`, `Depends-on`, `Expected Files`, `Verification` — plus a
one-line Objective and intent-only Tasks. These fields lift 1:1 into a
`tmp/prompts/{slug}/phase-N.md` header.

Authoring discipline (the durable/ephemeral split):
- **In the spec**: phase boundaries, the planning header, Expected Files,
  Verification skeleton — the *decomposition decision*.
- **NOT in the spec**: complete BEFORE/AFTER code blocks or full file bodies —
  those are the *executable lift*, authored later by `/create-implementation-plan`.

Single-phase or exploratory work MAY omit `## Phases`; `/create-implementation-plan`
then synthesizes the decomposition.

## Location

Specs live at `cortex://notes/system/specs/{slug}.md` where `{slug}` is canonical per
`@plan-slug-coherence_ws` (binds spec basename ≡ `todo:`/`plan:` entity ≡
phase_dir). The todo entity links via `source_uri: cortex://notes/system/specs/{slug}.md`.

## After the Draft

1. User reviews the draft
2. `/consult-architect` (or appropriate variant) reviews with codebase context
3. Incorporate feedback → update spec
4. Seed the plannable item with `/plan-seed {slug}` (atomic spec + `todo:` +
   `plan:` + `derived_from`), or wire the entities manually
5. Author phase docs per `/create-implementation-plan plan:{slug}` — a
   near-mechanical lift when the spec carries a `## Phases` section
