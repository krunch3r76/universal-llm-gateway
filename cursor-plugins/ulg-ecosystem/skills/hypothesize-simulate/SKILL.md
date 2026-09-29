---
name: hypothesize-simulate
description: "Answering seat — rival approaches, simulate, kill incumbent frame before bind. Fires without consult token."
trigger_match_terms: ["hypothesize-simulate", "hypothesize", "simulate", "rival", "architecture-suitability", "judgment_required", "none", "alternatives", "answering seat", "extraordinary aperture", "outside the box", "kill the conventional frame", "incumbent frame", "blank-world", "asymmetric search", "wide detent"]
related_skills: ["consult-posture", "path-sim", "reasoning-posture"]
---

# Hypothesize-simulate

Answering-seat rival fill. Cognitive procedure only — ¬ cascade · ¬ header · ¬ ranked tables · ¬ transport.

## Trigger

`answering_seat ∧ (purpose ∈ {ask, review} ∨ contract ∈ {consult, none} ∨ judgment_required ∨ retry(same_AC) ∨ operator_names(alternatives|simulate|hypothesize|extraordinary aperture|outside the box))`. `contract=none ⇒ apply`. Fires without consult token (`HYPOTHESIZE_SIMULATE_CONTRACTS` in `libs/reasoning_posture_contracts.py`).

## Refuse

`contract ∈ {implement, pure-mechanical} ⇒ skip`. `¬operator_consult_token ⇏ waive`. **`mechanical ⇒ skip` does not survive a failed attempt** — failed mechanical leg ⇒ `judgment_required` on second try; skip does not reach it.

## Steps

**Attachments** — fire procedure regardless of contract classification:
- **(a)** DISPOSITION `verdict` ∈ {`one_correction`, `transport_blocked`}: specimen 13068 red-test fault; 13070 live-ledger guard not settlement.
- **(b)** Second same-AC attempt after failed/red closeout: change kind not magnitude; specimen a:36685 13063→13068→13070.
- **(c)** Closeout/tool contradicts `assumed_state`: specimen 13042→13062 → `tests/conftest.py` autouse.
- **(d)** Operator "why is X?" on accepted state: specimen a:36694 work_key → 13061 `execution_mode`.
- **(e)** A rule you read on this seat and then broke fires the procedure; trace carries the specimen.
Falsifier: on that step: attachment moment without procedure or trace.

**Trace** — `run(procedure) ⇒ line-start `frame:`, `rival:`, `killed:` (≤3 lines). `¬trace ⇒ ¬ran`.
Falsifier: on that step: deliverable without three trace fields.

**Procedure** — `∀ bind: (1..9) ≺ bind`. Architectural change permitted; incumbent is baseline to kill.

1. Restate Q/OOS/deliverable; declare detent; self-select wide on wrong_kind.
   Falsifier: on that step: bind before Q or detent.

2. Name 2–3 kind-differing axes; wide ⇒ stack-killing axis; wide adds F1 incumbent name, F2 blank-world, F3 anti-magnitude.
   Falsifier: on that step: wide without stack-killer or F1–F3.

3. One rival per axis; reject correlated rivals.
   Falsifier: on that step: duplicate-axis rivals kept.

4. Current stack = baseline; `keep_and_add` ≠ default.
   Falsifier: on that step: incumbent shipped as answer.

5. Simulate each rival on 2–3 inputs; diagnosis: read primary evidence before rivals (13068/13070 specimen).
   Falsifier: on that step: rivals before traceback on diagnosis leg.

6. Correlated rivals or scaffold misfit ⇒ flag, down-weight, ¬skip.
   Falsifier: on that step: misfit ignored.

7. One-line dissent for front-runner.
   Falsifier: on that step: bind without dissent.

8. Bind one rival; name kills; one falsifying observation.
   Falsifier: on that step: bind without kills/falsifier.

9. No lit_search/RAG/paper_cite at instance; no ranked L0/L1/L2 (lead: `path-sim`).
   Falsifier: on that step: instance cited papers or L-tables.

| Detent | Width |
|---|---|
| `closed` | 2 axes, shallow sim |
| `standard` | 3 axes |
| `wide` | 3 axes + mandatory stack-killer + F1–F3 |
| `frontier` | defer L0 to `path-sim`, then run |

## Falsifier

Failed if bind preceded procedure, attachment fired without trace, or wide slate was magnitude-only (F3).
