---
name: consult-posture
description: "On operator consult \u2014 consult/consult Opus/run a consult/sealed ask \u2014 classify and load posture (detent, cascade, scope-lock) BEFORE transport. Not consult-a-file/docs."
---

# Consult Posture — extended-capability posture before transport

Owns trigger grammar, exemptions, posture-first ordering, defaults-by-reference, and composition glue ONLY. General Question/OOS/detent rails defer to `reasoning-posture` (resident). Machinery defers to `path-sim`; seat/transport to `consult-routing`; Anthropic-web substrate to `claude-ai-cdp-navigation`; when-to-pause to `advisor-timing`.

## When

`operator_consult_directive ⇒ classify_always ∧ load(posture) ≺ transport_choice ∧ load(posture) ≺ packet_composition`.

Fire on: bare `consult`, `consult Opus/Fable/Grok`, `run a consult`, `sealed ask/explore`, operator naming a greater model for a question, `frontier consult`, `second opinion from`.

**Not** incidental consult: consult a file, a calendar, the docs/skill (see Exempt).

## Procedure

1. **Classify** the utterance (exempt / downshift / full posture).
2. **Exempt** ⇒ ≤1-line note, proceed (no ceremony).
3. **Else declare posture BEFORE transport or packet:**
   - Pin scope-lock (Question + out-of-scope + deliverable shape — template SOT: `notes/system/specs/consult-scope-lock-template.md`)
   - Detent verdict + 1-line justification (`≤2 material sub-parts ⇒ closed` allowed)
   - **Hypotheticals fill (binding, all detents):** require the consulted seat to fill reasoning space with **decorrelated** rival **architectures / libraries / control flows** (thin L2) before binding an answer — **rivals must differ in approach, not merely in sampling seed; a set sharing a systematic error amplifies rather than brakes it.** **Elicit decorrelation, don't hope for it: first name 2–3 distinct approach axes (e.g. by data structure / by control-flow shape / by where the work happens / by what trades against what), then instantiate one rival per axis — IID re-sampling of "give me 3 alternatives" collapses to near-duplicates (MoC). Cap at a few rivals; naming axes must not expand into a fat matrix.** **This fill exists to brake *premature (representational) commitment* — the seat crystallizing on the first-fired representation before the space is filled: intuition sparks, research brakes.** Research-anchor gloss by reference (`path-sim` L2) is a **lexical spark/prime, not grounding.** The enumeration earns weight in the bind only when **rivals are decorrelated *and* the scaffold fits the task/model; on a correlated set or a mismatched scaffold, flag and down-weight the fill — do not treat it as a brake, and do not skip it.** Closed detent shrinks breadth/depth; it does **not** waive the fill. Full ranked L0∧L1∧L2 tables only when detent ≥ `standard` ∨ question/solution-space search is live (`path-sim`). **The two guards are thin self-checks on the already-enumerated rival set — no literature search, RAG, or paper citation is minted at the instance (bind A).**
   - **Anti-complexity / architecture-suitability (binding when live — friction a:27143):** when detent ≥ `wide` **and** the live objection is complexity accrual / architecture suitability (operator or arc), expanded-reasoning binders (Fable/Opus architect) **MUST** bind a **decorrelated alternative architecture** (retire/replace control plane, different admit substrate, tip-less structured work items, …) **or explicitly kill keep-and-add** before accepting additive gates/ceremony on the current stack. Ranking L1 patches that keep the same architecture and add validation/endpoints/atomic seeds is a **rival-fill miss** — treat as SCOPE-DRIFT, not an optimal L1 path. Packet authors must **not** pre-collapse the option set to endpoint-vs-validation (or other same-stack gate variants) when the Question is whether the kernel/admit stack itself is suitable — open approach axes that can kill the stack.
   - **Optional dissent beat (thin, closed-detent safe):** after leaning toward one rival, state in **one line** the strongest case the front-runner is wrong, before bind. Single-seat, one sentence — **not** a multi-agent debate (see demote guards).
   - Cascade roles: greater explores → lesser answers (per-family pairs by reference — defer to `path-sim`)
   - Substrate note when Anthropic-family web applies (defer to `claude-ai-cdp-navigation`)
4. **Then** load `consult-routing` and choose transport. Operator-named transport/model: obey AND posture still loads.

> **Anti-drift (binds A+B, 2026-07-19):** consult **default** = reasoning space + thin hypotheticals. Research-grounding lives in *doctrine/skill text* and as an **optional, encouraged-not-default** instance bonus — never the center. A consult **instance** carries only a thin, decorrelated rival-architecture/library/control-flow fill plus a research-anchor **gloss (lexical spark)** — it does **not** run a literature search, RAG, or cite papers. The decorrelation and scaffold-fit guards are self-checks on the rival set the seat already enumerated; they add zero external retrieval. \"All consults research-grounded\" / research-grounded-as-default is out of scope and must not be reintroduced.
>
> **Demote guards (thin anti-patterns, 2026-07-20):** **persona ≠ competence** — persona/domain framing shifts judgment and bias, not skill (`2311.10054`, `2311.04892`); the gloss is a lexical spark, a persona is not a competence lever. **prompt ≠ neutralize** — a neutrality/debiasing instruction does not erase an intrinsic prime; detect → flag → down-weight, never "prompted away" (`2509.08146`). **MAD ≠ default** — enumeration and the dissent beat stay single-seat and thin; do not escalate to N-way multi-agent debate as the default (`2502.08788`). These are *subtractions* — they add zero retrieval and remove levers, not add them.

**Origin=agent-initiated:** thinner default detent + extra scope skepticism.

**Operator downshift** (`quick consult`, etc.): honor + log + ¬ argue.

**Arc-scoped:** one scope-lock per arc; legs inherit via header grammar in `path-sim` (¬ restated here); per-leg detent re-declared.

## Exempt and downshift

| Class | Signal | Action |
|---|---|---|
| consult-economy | `consult the docs`, `consult the skill`, single-read suffices | ≤1-line note, proceed |
| lexical non-dispatch | `consult` in quoted text, incidental file/calendar sense | no posture block |
| operator downshift | `quick consult`, explicit lighter ask | fire + honor downshift |

Do **not** treat `consult me` / `consult the operator` as exempt without operator confirmation.

## FOL invariants

1. `operator_consult_token ∧ ¬exempt ⇒ load(posture) ≺ transport_choice ∧ load(posture) ≺ packet_composition`
2. `operator_names_transport ⇒ obey(transport) ∧ still_load(posture)`
3. `¬copy(path_sim_machinery) ∧ (machinery ⇒ defer(agent_skill:path-sim)) ∧ (¬resident(path-sim, seat) ⇒ annex URI: cortex://notes/system/templates/fable-path-sim-prompt.md)`
4. `exempt ⇔ lexical_non_dispatch(consult) ∨ single_read_suffices`; `≤2_material_sub_parts ⇒ fire ∧ verdict(detent=closed)` — sub-parts rule lives inside the verdict, not at the trigger
5. `detent_ceiling = f(verifier_strength) ⇒ defer(decision:verifier-detent a:24920)`; consult may supply external verification — cite, don't restate
6. `cascade_default: greater_explores ∧ lesser_answers(closed_aperture)` — per-family pairs BY REFERENCE only
7. `∀ ¬exempt consult: activate(hypotheticals_fill) ≺ answer_bind` — fill = **decorrelated** rival architectures/libraries/control flows (+ path-sim L2 research-anchor gloss **= lexical spark, ¬grounding**); **`brake_weight(fill) ⇐ decorrelated(rivals) ∧ scaffold_fits(task,model)`; `¬decorrelated ∨ ¬fit ⇒ flag ∧ down-weight` (¬ waive, ¬ skip — fill still runs);** `detent=closed ⇒ thin_fill` (not waive); `detent≥standard ∨ space_search ⇒ path-sim tables`; **`¬(lit_search ∨ RAG ∨ paper_cite)@instance` (bind A)**; **`elicit(decorrelation) = axes_first`: `name(2..3 approach_axes) ≺ instantiate(one rival / axis)` (¬ IID re-sample); `dissent_beat = optional ∧ thin ∧ single_seat` (¬ MAD); `fill ⊨ brake(premature_commitment)` (intuition sparks, research brakes); demotes: `persona ≠ competence ∧ prompt ≠ neutralize(prime) ∧ MAD ≠ default`.**
8. `seat_or_transport_rule ∈ body ⇒ F4_violation ⇒ excise_to(consult-routing) ∨ retire(this)`
9. `anthropic_family_web ⇒ defer(claude-ai-cdp-navigation)`; attest picker label, not body prose
10. `operator_downshift ⇒ honor ∧ log ∧ ¬argue` · `origin=agent_initiated ⇒ thinner_default ∧ scope_skepticism↑` — downshift may thin the fill, ¬ erase it unless operator explicitly waives hypotheticals
11. `consult_arc ⇒ one_scope_lock ∧ legs_inherit(path-sim header grammar) ∧ per_leg_detent_redeclared`
12. `fire ⇒ conformance_row_logged`; miss ⇒ `friction(owner=agent_skill:consult-posture)` into path-sim Stage-2/3 evidence stream
13. `detent≥wide ∧ objection∈{complexity_accrual, architecture_suitability} ⇒ bind(decorrelated_alt_architecture) ∨ kill(keep_and_add) ≺ accept(additive_gate_on_current_stack)` — keep-and-add under that objection = rival-fill miss / SCOPE-DRIFT (¬ optimal L1); `packet_author ⇒ ¬pre_collapse(option_set, same_stack_gate_variants)` when Question challenges kernel/admit suitability (friction a:27143; machinery: `path-sim` L2)

## Composition table

| Concern | Defers to | Non-resident fallback |
|---|---|---|
| Resident Question/OOS/detent/cascade rails (thinking-off non-waiver) | `agent_skill:reasoning-posture` | alwaysApply stub `reasoning-posture_ulg` |
| Epistemic quality (steelman / calibration / courage) — **paired** with reasoning-posture | `agent_skill:frontier-reasoning-discipline` | cortex_brief opcontext block |
| Path-sim machinery · detents · header grammar · checklist · per-family window params | `agent_skill:path-sim` | `cortex://notes/system/templates/fable-path-sim-prompt.md` |
| Seat/transport/densify/implement_ready | `consult-routing` | `agent_bus` ask code lead |
| Anthropic-web substrate + attest | `claude-ai-cdp-navigation` | — (Cursor-lane concern) |
| When to pause/consult at all | `advisor-timing` | — |
| Detent ceiling ↔ verifier strength | `decision:verifier-detent` (a:24920) | — |
| Scope-lock field shape | `cortex://notes/system/specs/consult-scope-lock-template.md` | field 5 carries slug line |

## Related skills

- reasoning-posture
- frontier-reasoning-discipline
- path-sim
- consult-routing
- advisor-timing
- claude-ai-cdp-navigation

