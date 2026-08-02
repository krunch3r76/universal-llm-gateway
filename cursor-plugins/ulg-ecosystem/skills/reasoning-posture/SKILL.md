---
name: reasoning-posture
description: "Resident reasoning posture — pin Question before merits; out-of-scope; detent before widening; cascade greater→lesser when live; thinking-off does not waive."
---

# Reasoning Posture — generalizable procedural rails

Owns Question/OOS/detent/cascade-ordering invariants for strong reasoning models.
Does **not** own consult trigger grammar (`consult-posture`), seat/transport
(`consult-routing`), L0/L1/L2 machinery (`path-sim`), epistemic quality
(`frontier-reasoning-discipline`), or determinacy (`presence-discipline`).

## When

Resident on Cursor via alwaysApply stub. Load this body on demand (or on
claude.ai via Customize Skills / `Use the reasoning-posture skill`) when
material judgment, consult, path-sim, or proposal review is live.

**Pairing (BINDING):** `material_judgment ⇒ also Use the frontier-reasoning-discipline skill`.
Scope without epistemic quality is an incomplete inject. Mechanical executor seats
against a dense pre-staged spec are exempt (dispatcher owns discipline).

## Invariants

1. `pin(Question) ≺ merits` — operator-seeded wording when available
2. `declare(Out-of-scope)` — load-bearing negative; silent rescope = failure class
3. `declare(detent ∨ aperture) ≺ widen` — thin/closed allowed with 1-line justification
4. `cascade_live ⇒ greater_explores ∧ lesser_binds` — per-family pairs by reference (`path-sim`)
5. `consult_live ⇒ posture ≺ transport` — fire-gated via `consult-posture`
6. `thinking_off ⇏ waive(1..5)` — residency ≠ effort; rails are more load-bearing without thinking
7. `material_judgment_injection ⇒ pair(this, frontier-reasoning-discipline)` — inject sites
   (mission chips, stubs, MCP opcontext, CDP nudges) MUST not ship this without the pair

## Procedure (cheap)

| Step | Action |
|---|---|
| 1 | State **Question** (verbatim when operator-seeded) |
| 2 | State **Out-of-scope** |
| 3 | If widening or multi-model: declare detent/aperture before expanding |
| 4 | If cascade: greater explores → lesser answers/binds |
| 5 | If operator consult token: Use the `consult-posture` skill, then transport |

Scope-lock field shape (consult/path-sim): `cortex://notes/system/specs/consult-scope-lock-template.md`.

## Composition

| Concern | Owner |
|---|---|
| These rails (resident) | **this skill** |
| Consult fire grammar / exemptions / posture-before-transport ordering | `consult-posture` |
| When to pause at all | `advisor-timing` |
| Recon-before-implement intake | `recon-default` / `cheap-recon-before-escalation` |
| L0/L1/L2 · header · per-family windows | `path-sim` |
| Steelman / confidence / courage | `frontier-reasoning-discipline` |
| Bind forks / one step / evidence | `presence-discipline` |

## Anti-patterns

| Bad | Good |
|---|---|
| Jump to merits without Question/OOS | Pin scope first |
| Widen aperture silently | Detent verdict first |
| Inject `/reasoning-posture` without `/frontier-reasoning-discipline` on a substantive seat | Pair both chips / Use lines |
| Fat `consult-posture` into general reasoning guide | Keep consult fire-gated; reference this |
| Gate alwaysApply on thinking knobs | `thinking_off ⇏ waive` |
| Copy path-sim machinery here | Defer by reference |

## Related skills

- consult-posture
- advisor-timing
- path-sim
- frontier-reasoning-discipline
- presence-discipline
- cheap-recon-before-escalation
