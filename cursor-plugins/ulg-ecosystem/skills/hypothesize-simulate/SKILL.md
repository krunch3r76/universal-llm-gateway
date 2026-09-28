---
name: hypothesize-simulate
description: "Answering seat on ask, review, consult, none, or judgment_required — rival approaches, simulate, kill the incumbent frame. Execute before bind; fires without a consult token."
trigger_match_terms: ["hypothesize-simulate", "hypothesize", "simulate", "rival", "architecture-suitability", "judgment_required", "none", "alternatives", "answering seat", "extraordinary aperture", "outside the box", "kill the conventional frame", "incumbent frame", "blank-world", "asymmetric search", "wide detent"]
related_skills: ["consult-posture", "path-sim", "reasoning-posture"]
---

# Hypothesize-simulate — answering-seat rival fill

Reader = answering model. Cognitive procedure only. ¬ cascade · ¬ header grammar · ¬ ranked tables · ¬ transport.

## When — answering seat (ask / review / consult / none / judgment_required); fires without an operator consult token

`answering_seat ∧ (purpose ∈ {ask, review} ∨ contract ∈ {consult, none} ∨ density_triage=judgment_required ∨ retry(same_AC | failed ∨ red closeout) ∨ operator_names(alternatives|simulate|hypothesize|extraordinary aperture|outside the box)) ⇒ apply`

`contract=none ⇒ apply` — that leg leaves the option space to the seat, so it needs the fill more than `consult`, which arrives with a pinned Question and scope-lock. GIW `resolve_prompt_preamble` and Stargate handoff enrich inject the Use-line on exactly this set (`libs/reasoning_posture_contracts.py::HYPOTHESIZE_SIMULATE_CONTRACTS`) — the body gate and the injection predicate are one set, not two.

`contract ∈ {implement, pure-mechanical} ⇒ skip`. `¬operator_consult_token ⇏ waive`. **`mechanical ⇒ skip` does not survive a failed attempt** — a mechanical leg that fails once is `judgment_required` on its second attempt, and the skip line does not reach it. Dispatcher glue: `consult-posture`. Ranked L0/L1/L2 tables: `path-sim` (lead). Rails: `reasoning-posture`.

§ Attachment points names four artifacts that fire the procedure whatever this predicate's classification terms say.

## Attachment points (BINDING)

`∀ moment ∈ {a, b, c, d}: fire(procedure) ∧ ¬gate(contract_classification)`. The predicate above fires on the reader judging a leg `judgment_required` — precisely the judgment a wrong frame is invisible to. These four attach to artifacts instead.

- **(a) Authoring a DISPOSITION whose `verdict` is `one_correction` or `transport_blocked`.** A non-ratify verdict says the incumbent frame already failed once; disposition is the act that re-seats it. Specimen: the 13068 DISPOSITION named a code fault from a red-test list alone, and 13070's traceback showed the tests were red from the live-ledger guard.
- **(b) Any second attempt at the same AC after a failed or red closeout.** `retry(same_AC) ⇒ F3 mandatory` — the retry changes **kind** (evidence source, mechanism, or frame), not **magnitude** (same route, more effort, bigger model), or it is declared a resample and ¬ fired. Specimen: a:36685 attempts 13063 → 13068 → 13070, where the second and third were magnitude variants of the first (same seat family, same five red tests, "try harder", then "escalate the model").
- **(c) A closeout or tool result that contradicts the packet's `assumed_state`.** The contradiction is evidence against the frame, not against the execution. Specimen: 13042 → 13058/13064, where "re-run the tests so the harvester sees them" held until 13062 failed and the kind-change — no seat should ever need the env prefix, hence `tests/conftest.py` autouse ledger isolation — became reachable.
- **(d) An operator question of the form "why is X the case?" about a state the seat had accepted.** That question is a frame probe, not a request for detail. Specimen: a:36694, where "remember to pass `work_key` on the wire" survived until the operator asked why lanes were serial, yielding cursor-auto derives it and states `execution_mode` on the receipt (13061).

## Trace (BINDING)

`run(procedure) ⇒ artifact carries {frame:, rival:, killed:}` as line-start fields. Artifact = whatever the procedure feeds — bus turn, DISPOSITION, DIRECTIVE, reply.

| Field | One line |
|---|---|
| `frame:` | the incumbent you were about to recede into |
| `rival:` | the bound alternative, naming the axis it differs on |
| `killed:` | what the bind makes unreachable — or `none — incumbent kept` plus the one-line dissent |

Three lines, no more; the ≤2000-char DISPOSITION target is the budget. `¬trace ⇒ ¬ran`, whatever the reasoning said — an unwritten rival is unauditable, and silent application is the failure this section closes.

## Procedure — execute in the deliverable before bind

`∀ bind: procedure(1..9) ≺ bind`

1. Restate Question / OOS / deliverable. Declare detent (`closed|standard|wide|frontier`). Self-select — don't wait to be told: `objection(current_stack) = wrong_kind ⇒ self_select(wide)`; `objection = wrong_tuning ⇒ standard suffices`. Operator vocabulary (§ Wide detent) is a sufficient trigger, never a necessary one.
2. Name 2–3 approach axes that differ in kind. Code: data structure · control flow · where work happens · who reads it · what trades against what. Non-code: who bears the cost · what is actually exchanged · whose resource or attention is consumed · what would have to be true for the incumbent to be the wrong *kind* of answer. `detent=wide ⇒ ∃ axis that can kill(current_stack)`.
3. Instantiate exactly one rival per axis. `rival_i ≈ resample(rival_j) ⇒ reject` (shared systematic error).
4. Keep current stack as baseline. `keep_and_add ∈ rivals ∧ ¬default`.
5. Simulate every rival on 2–3 concrete inputs: inputs → predicted outcome → failure modes → what becomes unreachable. `leg = diagnosis ⇒ input₁ = the primary evidence artifact itself` (traceback, payload, row), read before any rival is instantiated. Specimen 13068/13070: rivals instantiated over a red test list with the traceback unread all inherited the same wrong premise — the tests were red from the live-ledger guard, not the settlement code.
6. `correlated(rivals) ∨ scaffold_misfit(task, model) ⇒ flag ∧ down-weight ∧ ¬skip`.
7. One-line dissent: strongest case the front-runner is wrong.
8. Bind one rival; name what it kills; state the falsifier (one observation that overturns the bind).
9. `¬(lit_search ∨ RAG ∨ paper_cite)@instance` · `¬ranked(L0|L1|L2 tables)` — those are the lead's check (`path-sim`).

## Architectural change is permitted

`apply(this) ⇒ architectural_change ∈ option_space`. The incumbent implementation is the baseline to kill, not a constraint the bind must fit. A producer that already holds the bytes may be redesigned so a stream it already drains is retained and served. A packet line such as "do not invent a second store" does not freeze that producer. Treating today's drop, skip, or missing route as the option space is the miss.

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
| Attachment on operator lanes | `cdp-operator-proxy` (DISPOSITION duty) |
| Domain instances · `path-sim` L2 enrichment | `runbook:extraordinary-aperture` |
