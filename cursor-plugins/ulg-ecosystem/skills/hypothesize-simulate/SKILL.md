---
name: hypothesize-simulate
description: "When you are the answering seat on ask, review, consult, or judgment_required — rival approaches, simulate, architecture-suitability — execute this before bind. Fires without operator consult token."
trigger_match_terms: ["hypothesize-simulate", "hypothesize", "simulate", "rival", "architecture-suitability", "judgment_required", "alternatives", "answering seat"]
related_skills: ["consult-posture", "path-sim", "reasoning-posture"]
---

# Hypothesize-simulate — answering-seat rival fill

Reader = answering model. Cognitive procedure only. ¬ cascade · ¬ header grammar · ¬ ranked tables · ¬ transport.

## When — answering seat (ask / review / consult / judgment_required); fires without an operator consult token

`answering_seat ∧ (purpose ∈ {ask, review} ∨ contract=consult ∨ density_triage=judgment_required ∨ operator_names(alternatives|simulate|hypothesize)) ⇒ apply`

`¬operator_consult_token ⇏ waive`. Dispatcher glue: `consult-posture`. Ranked L0/L1/L2 tables: `path-sim` (lead). Rails: `reasoning-posture`.

## Procedure — execute in the deliverable before bind

`∀ bind: procedure(1..9) ≺ bind`

1. Restate Question / OOS / deliverable. Declare detent (`closed|standard|wide|frontier`).
2. Name 2–3 approach axes that differ in kind (data structure · control flow · where work happens · who reads it · what trades against what). `detent=wide ⇒ ∃ axis that can kill(current_stack)`.
3. Instantiate exactly one rival per axis. `rival_i ≈ resample(rival_j) ⇒ reject` (shared systematic error).
4. Keep current stack as baseline. `keep_and_add ∈ rivals ∧ ¬default`.
5. Simulate every rival on 2–3 concrete inputs: inputs → predicted outcome → failure modes → what becomes unreachable.
6. `correlated(rivals) ∨ scaffold_misfit(task, model) ⇒ flag ∧ down-weight ∧ ¬skip`.
7. One-line dissent: strongest case the front-runner is wrong.
8. Bind one rival; name what it kills; state the falsifier (one observation that overturns the bind).
9. `¬(lit_search ∨ RAG ∨ paper_cite)@instance` · `¬ranked(L0|L1|L2 tables)` — those are the lead's check (`path-sim`).

## Width by detent

| Detent | Width |
|---|---|
| `closed` | 2 axes, shallow sim |
| `standard` | 3 axes |
| `wide` | 3 axes + stack-killing axis mandatory |
| `frontier` | defer L0 to `path-sim`, then run this |

## Related

| Concern | Owner |
|---|---|
| Dispatcher glue / load | `consult-posture` |
| Ranked tables / cascade / header | `path-sim` |
| Question / OOS / detent rails | `reasoning-posture` |
