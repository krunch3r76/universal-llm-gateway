---
name: hypothesize-simulate
description: "Answering seat on ask, review, consult, light-bounded, or judgment_required — rival approaches, simulate, kill the incumbent frame. Execute before bind; fires without a consult token."
trigger_match_terms: ["hypothesize-simulate", "hypothesize", "simulate", "rival", "architecture-suitability", "judgment_required", "light-bounded", "alternatives", "answering seat", "extraordinary aperture", "outside the box", "kill the conventional frame", "incumbent frame", "blank-world", "asymmetric search", "wide detent"]
related_skills: ["consult-posture", "path-sim", "reasoning-posture"]
---

# Hypothesize-simulate — answering-seat rival fill

Reader = answering model. Cognitive procedure only. ¬ cascade · ¬ header grammar · ¬ ranked tables · ¬ transport.

## When — answering seat (ask / review / consult / light-bounded / judgment_required); fires without an operator consult token

`answering_seat ∧ (purpose ∈ {ask, review} ∨ contract ∈ {consult, light-bounded} ∨ density_triage=judgment_required ∨ operator_names(alternatives|simulate|hypothesize|extraordinary aperture|outside the box)) ⇒ apply`

`contract=light-bounded ⇒ apply` — that leg leaves the option space to the seat, so it needs the fill more than `consult`, which arrives with a pinned Question and scope-lock. GIW `resolve_prompt_preamble` and Stargate handoff enrich inject the Use-line on exactly this set (`libs/reasoning_posture_contracts.py::HYPOTHESIZE_SIMULATE_CONTRACTS`) — the body gate and the injection predicate are one set, not two.

`contract ∈ {implement, pure-mechanical} ⇒ skip`. `¬operator_consult_token ⇏ waive`. Dispatcher glue: `consult-posture`. Ranked L0/L1/L2 tables: `path-sim` (lead). Rails: `reasoning-posture`.

## Procedure — execute in the deliverable before bind

`∀ bind: procedure(1..9) ≺ bind`

1. Restate Question / OOS / deliverable. Declare detent (`closed|standard|wide|frontier`). Self-select — don't wait to be told: `objection(current_stack) = wrong_kind ⇒ self_select(wide)`; `objection = wrong_tuning ⇒ standard suffices`. Operator vocabulary (§ Wide detent) is a sufficient trigger, never a necessary one.
2. Name 2–3 approach axes that differ in kind. Code: data structure · control flow · where work happens · who reads it · what trades against what. Non-code: who bears the cost · what is actually exchanged · whose resource or attention is consumed · what would have to be true for the incumbent to be the wrong *kind* of answer. `detent=wide ⇒ ∃ axis that can kill(current_stack)`.
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

## Wide detent — kill the incumbent frame

`detent=wide ⇒ procedure(1..9) ∪ {F1, F2, F3}`. Operator vocabulary: extraordinary aperture · outside the box · kill the conventional frame · asymmetric search. The step-1 self-select criterion mirrors `path-sim` § Aperture detents by design — kept inline (not link-out) so wide is self-triggerable on surfaces where `path-sim` cannot load, not only when named.

- **F1 — Name the incumbent frame** you are about to recede into, *before* naming axes (step 2). That frame is the baseline, never the answer. `¬named(incumbent) ⇒ ∀ rival: silently_inherits(incumbent)`.
- **F2 — Blank-world test.** Suppose no inherited lead, pin, plan, or strategy exists — what class of move remains? Run before instantiating rivals; this is the generator for the stack-killing axis step 2 requires at `wide`.
- **F3 — Resample-of-incumbent failure test.** `∀ rival ∈ survivors: rival ≈ magnitude_variant(incumbent) ⇒ declare_failure`. Same payer, same instrument, same book, same substituted labor, one more input of a kind already tried — all magnitude, no kind. Say the widen failed; ¬ ship a dressed-up conventional list.

`F3 ≠ step 3`. Step 3 rejects rivals that duplicate *each other*; F3 rejects a slate that duplicates the *baseline*. A slate passes 3 and fails F3 whenever the rivals are decorrelated from one another but all rest on the incumbent's premise.

Domain instances and optional `path-sim` L2 enrichment (Cursor only): `runbook:extraordinary-aperture`.

## Related

| Concern | Owner |
|---|---|
| Dispatcher glue / load | `consult-posture` |
| Ranked tables / cascade / header | `path-sim` |
| Question / OOS / detent rails | `reasoning-posture` |
| Domain instances · `path-sim` L2 enrichment | `runbook:extraordinary-aperture` |
