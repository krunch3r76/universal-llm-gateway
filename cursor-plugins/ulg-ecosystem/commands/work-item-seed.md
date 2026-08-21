Work-item seed — friction/idea → closable work item that `/layer` can enter correctly.

**This command wraps a skill.** Machinery SOT: Use the `work-item-seed-path` skill
(§ When · § Stages S1–S6 · § Mint modes · § Architecture order A/B · § Handoff · § Anti-patterns).
Full stage rationale: `cortex://notes/system/specs/work-item-seed-path.md`. This file is a thin
wrapper — ¬ re-derive stages, mint modes, or attach duty here.

Feeds `/layer`; does **not** replace `path-sim` (non-codework) or `/layer` G1–G6. Headless /
cursor-auto / CDP enter by skill slug with no command layer.

## When

| Condition | Route |
|---|---|
| Operator `/work-item-seed …` | This command → skill |
| Feature-add or investigate+fix needs a **new** todo (or backlog park) | This path |
| Architecture open; rich-seed would harden the wrong shape | Skill · prefer mode B (Fable-before-seed) |
| Actionable friction; minting is the next act | This path (cite assertion id) |
| Todo already exists ∧ **no** Mode B / arch-consult ask | `/layer todo:{slug}` — not this command |
| Todo already exists ∧ Mode B / Fable / architecture consult asked | **This path** · Mode B on existing slug · ¬ remint · S5 attach → `/layer` G2 |
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
/work-item-seed mode=fable-before-seed {idea}
/work-item-seed mode=seed-then-layer {idea}
```

`mode=fable-before-seed` ⇒ skill S3 mode B. `mode=seed-then-layer` ⇒ mode A. Omit mode ⇒ skill
evaluates § S3 mandatory triggers (≥2 recon forks, invariant-touching feature-add, etc.) —
**not** silent default to mode A. Phrases like "include fable architectural consult" /
"consult-before-seed" / "architecture-open" also force Mode B.

Bare `/work-item-seed` with **no idea text** ⇒ halt; ask for friction/idea (or `friction a:{id}`).

## Lead obligations

Load the skill and run S1→S6 in order. **Publish stage disposition before S4.**

### Mode B admit-proof gate (BINDING)

When S3 mode B is mandatory (`mode=fable-before-seed`, operator asked Fable/arch consult, or
skill triggers fire):

1. Same turn as the Mode B disposition claim: either
   - **Admit:** `team_dispatch(model=cdp/fable|cdp/opus-5, …)` returns `execution_id` +
     `poll_hint` (quote those fields), **or**
   - **Honest halt:** name the concrete blocker (validation/CDP capacity/tool error) — ¬ claim
     firing succeeded.
2. **Forbidden:** prose-only "staging then firing" / "will dispatch Fable" then end the turn
   with no admit payload and no named halt.
3. Harvest → **then** mint (or S5 attach on existing slug) — ¬ skip Fable because the command
   omitted `mode=`.

Compose `/todo` rich-seed for mint field mechanics — ¬ fork field lists into this command.
After mint (+ attach when consult ran), hand off to `/layer` at the gate the skill names.

## Skills

Use the `work-item-seed-path` skill (SOT) · `abstraction-layering` (Stage 0 attach / `/layer`) ·
`friction-review` (channel triage) · `cheap-recon-before-escalation` · `consult-routing`
