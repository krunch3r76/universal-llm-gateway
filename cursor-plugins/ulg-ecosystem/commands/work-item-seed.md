Work-item identity — punch slug/kind/density, then spawn conductor.

`seed ≠ Architect / Sketch.` This command is the S4a identity punch. Long
reasoning is G1/S4b on the conductor.

**This command wraps a skill.** Natural language (“loop Fable”, “architectural
guidance”, “commission this feature”) fires the same path — the slash is optional.
Machinery SOT: Use the `work-item-seed-path` skill
(§ When · § Stages S1–S6 · § Mint modes · § Architecture order A/B · § Spawn · § Anti-patterns).
Full stage rationale: `cortex://notes/system/specs/work-item-seed-path.md`. This file is a thin
wrapper — ¬ re-derive stages, mint modes, or attach duty here.

**S4a stays on this path. S4b + G1–G6 stay on the conductor.** ¬ replace `path-sim`
(non-codework). ¬ conductor chooser for `/path-sim` vs `/layer`. Bind:
`layer-conductor-unify` §3.1. Headless / cursor-auto / CDP enter by skill slug with
no command layer.

## When

| Condition | Route |
|---|---|
| Operator `/work-item-seed …` | This command → skill |
| Feature-add or investigate+fix needs a **new** todo (or backlog park) | This path · S4a then spawn |
| Architecture open; rich-seed would harden the wrong shape | This path · **S4a still fires** · stamp Mode B (Fable-before-S4b) for the conductor |
| Actionable friction; minting is the next act | This path (cite assertion id) |
| Todo already exists ∧ **no** Mode B / arch-consult ask | Re-admit conductor — not this command |
| Todo already exists ∧ Mode B / Fable / architecture consult asked | Re-admit conductor on existing slug · ¬ remint |
| `{idea}` matches an open todo (slug / `todo:conductor-{idea}` / name stem / same-session NL punch) | **S0 lookup** in the skill · re-admit or halt · ¬ remint · ¬ second spawn |
| Friction observation only | `friction()` via friction-review — stop |
| Feature ask, not commissioned | `friction(category=feature)` via friction-review — stop; ¬ mint |
| Non-codework Q→A | `/path-sim` |
| Multi-phase plannable | `/plan-seed` |
| Settled bind, ship only | `/address` |

## Invocation

```
/work-item-seed {idea}
/work-item-seed friction a:{assertion_id}
/work-item-seed friction {assertion_id}
/work-item-seed mode=B {idea}
/work-item-seed mode=A {idea}
```

`mode=B` (aliases: `fable-before-S4b`, `fable-before-seed`) ⇒ stamp Mode B for the
conductor. `mode=A` (alias: `seed-then-layer`) ⇒ Mode A. Omit mode ⇒ skill
evaluates § S3 mandatory triggers — **not** silent default to mode A. Phrases
like "include fable architectural consult" / "consult-before-seed" /
"architecture-open" also stamp Mode B.

Bare `/work-item-seed` with **no idea text** ⇒ halt; ask for friction/idea (or `friction a:{id}`).

## Lead obligations

Load the skill and run **S0 lookup** then S1→S4a→S6. **Publish stage disposition (S0–S6) before S4a.**
S0 hit (open todo for this idea, including a same-session NL punch) ⇒ SKIP S4a and S6; re-admit or halt-with-pointer.
**¬ halt S4a for Mode B** when S0 missed. IDE does **not** fire Mode B Fable on this command.

Compose `/todo` identity mint only — Problem/Scope/Acceptance may be sparse.
Friction-sourced S4a **must** stamp `spawned_by_friction=<int>` (todo-done auto-closes that parent; `"a:{id}"` / `derived_from_friction` is not the close key).
After S4a, spawn `packet_kind=conductor` per skill § S6 — Composer (`omit model=`,
`{fast:true}`); judgment nests CDP. Pass `dispatch_thread_id` ∈ {continuity root with
turns, pending-empty child of root}.
Receipt identity is the admitted thread + `branch_current=cursor-sdk/lane-{that id}`.

Conductor fault after spawn: operator names the lane to the sitting liaison
(specimen 9638). Debug lives there. ¬ rewrite this command into that house's
drop list.

Mode B admit-proof binds the **conductor CHECKPOINT**, not this IDE turn.

## Skills

Use the `work-item-seed-path` skill (SOT) · `abstraction-layering` (G-ladder shape) ·
`friction-review` (channel triage) · `cheap-recon-before-escalation` · `consult-routing`
