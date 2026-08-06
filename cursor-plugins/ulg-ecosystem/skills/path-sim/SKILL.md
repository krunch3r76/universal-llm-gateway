---
name: path-sim
description: "On dispatching or running a frontier consult that searches a solution or question space — L0/L1/L2 machinery, aperture detents, scope-lock handshake, conformance checklist."
lifecycle: active
trigger_match_terms: ["path-sim", "path_sim", "path simulation", "aperture", "detent", "L0", "L1", "L2", "question space", "solution space", "scope-lock", "frontier consult", "tree of thoughts", "cascade", "effort dialing", "charter-runner", "enroll_charter_runner", "tick enrollment", "hang on tick"]
related_skills: ["reasoning-posture", "frontier-reasoning-discipline", "consult-routing", "dispatch-workflow", "handoff-packet-authoring", "frontier-model-instructions"]
---

# Path-Sim — frontier solution/question-space search

**v0 provisional** — Stage 1 of `decision:fable-path-sim-remaining-window`; ratify/revise at window close after ≥2 dogfood reps (annex C § Staging). Edits land HERE (SOT); the template annex (`notes/system/templates/fable-path-sim-prompt.md`) and window params on the decision entity regenerate from this — never fork a second body.

Composes with `notes/system/specs/consult-scope-lock-template.md` (scope-lock pins the QUESTION; path-sim opens the SOLUTION SPACE inside it — wide aperture without a pinned question = drift), `reasoning-posture` + `frontier-reasoning-discipline` (paired scope + epistemic rails; this skill is the consult-shaped extension).

**L3 annexes (same directory — open only for the named job).** **Annex A** `dispatch-cascade-annex.md`: orchestrating an arc — commands, phase table, recon, R positions, operator-framed Q, Q-only / Q-cascade, bundled dispatch, Gate-2 densify closeout, R-admit CDP recipe + poll ladder, auto-advance checklist, Stage-B, anti-patterns, todo attrs. **Annex B** `tick-enrollment-annex.md`: charter-tick enrollment CHECKPOINT template, **autonomous** attendance. **Annex C** `substrate-staging-annex.md`: transport tables (per-family / Anthropic / xAI), VISION-ALIGN corpus + note rule, docstring AC, delivery lanes, staging, grounding.

## When

`dispatch ∨ run(frontier_consult) ∧ searches(solution_space ∨ question_space) ⇒ apply`.

**Codework carve-out (`decision:abstraction-layering`, bound operator 2026-07-27).** A request to *change the codebase* does **not** run a path-sim ratification window — it runs the **abstraction layering** lane: `architecture → frame → densify → check → implement`, entered at the highest still-open layer, ratification **inherited** from the layer above. Use the `abstraction-layering` skill (command `/layer`). ¬ open R-admit / R-after path-sim windows on a codework arc. **Not superseded:** non-codework question/solution-space search, and the fat-packet deepen lane (`workflow:path-sim-fat-packet-lane`) — those stay here. Where both would apply to codework, layering wins. Charter-tick enrollment (annex B) still runs this skill's arc: the tick kernel rewrite **landed** (Phase 3 cutover, kernel is sole admitter) **without** adding a layer-specific materializer, so path-sim remains what an enrolled root materializes — a standing state, not an interim one (`cortex://notes/system/specs/charter-tick-kernel-rewrite.md`).

Also: `consult_posture_fire ∧ ¬exempt ⇒ thin_L2_hypotheticals_fill` even when full path-sim tables are not required (operator bind 2026-07-19 — every consult fills reasoning space with **decorrelated** rival architectures/libraries/control flows). **The fill brakes only while rivals are decorrelated and the scaffold fits the task/model; a correlated set or a mismatched scaffold is flagged and down-weighted at bind time — not treated as a brake, not skipped.** **Optional thin dissent beat (one line, single-seat) may follow the fill before bind; keep it single-seat — N-way multi-agent debate is not the default (`consult-posture` demote guards).**

Closed aperture: `|material_sub_parts| ≤ 2 ⇒ detent=closed` — wide L0 branching and deep tables are redundant; **thin L2 fill still runs** (few **decorrelated** rivals + research-anchor gloss **as lexical spark**), then Opus-shaped answer under the closed aperture. ¬ skip straight to a single bind with an empty hypotheticals space.

## Cascade principle (operator bind 2026-07-16)

The *greater* reasoning pass helps the *lesser* look at the right places — first explore/sharpen the question space (including whether the current architecture is suitable), then answer. `Fable→Opus` and `Opus→Opus` both use this cascade. A wider/deeper pass runs L0 (+ optional deep L1/L2) and hands ranked tables to a High-effort answer pass under a narrower aperture.

## Three layers

- **L0 — QUESTION SPACE.** Enumerate material questions that unlock the solution space, including questions challenging architecture suitability. Per candidate: why it matters · what it opens/closes · what a good answer looks like. Rank `ask-now / defer / kill`. Output = ranked question table + one recommended Question (set) for the next packet.
- **L1 — CURRENT STRUCTURE.** Enumerate material solution paths under existing seats, substrates, invariants, SOTs. Per path: inputs → simulated outcome → failure modes → what becomes unreachable. Rank `optimal / acceptable / kill`.
- **L2 — HYPOTHETICAL ARCHITECTURES / LIBRARIES / CONTROL FLOWS.** Enumerate alternate architectures, libraries, and control flows (rename / relocate / split / retire / introduce primitive). Per hypothetical re-run the path search: which solutions open, which close, cost of the change. Rank candidates **separately** from L1. **Enumerate *decorrelated* alternatives — each a distinct approach, not a re-sampling of one; a set sharing a systematic error amplifies it rather than correcting it. Elicit that decorrelation axis-first: enumerate distinct approach *concepts/axes*, then generate one alternative per concept (Mixture-of-Concepts); "propose N alternatives" IID-sampled collapses to near-duplicates. The fill's target failure is *premature/representational commitment* — crystallizing on the first representation before the space is enumerated. The enumeration is weighed only when the scaffold fits the task/model — a mismatched scaffold can backfire (flag and down-weight, do not force). Both checks are cheap self-assessments of the set already listed; neither is a literature search.** ¬ “precedents” (legal sense) — L2 objects are structural/causal alternatives. **Anti-complexity bind (friction a:27143):** when detent ≥ `wide` and the live objection is complexity accrual / architecture suitability, L2 **fails its job** if the recommended bind is keep-and-add (same architecture + new validation/endpoint/atomic-seed/ceremony). That outcome is a **rival-fill miss / SCOPE-DRIFT**, not an optimal L1 path — the binder must surface at least one decorrelated alternative that can *kill* the current stack (or explicitly kill keep-and-add) before accepting additive hardening. Packet authors must not pre-collapse options to same-stack gate variants when the Question challenges kernel/admit suitability (`consult-posture` FOL 13). **Research-anchor gloss (lexical prime/spark, not extra machinery, not grounding):** Tree of Thoughts / deliberate search · neural architecture search · quality-diversity / POET · counterfactual rollouts · antecedents → consequents (causal). Use these names beside the operational objects so pretrained research neighborhoods fire; do not replace the L2 procedure.

Machinery rules:
- `∀ material timeline: deepen until failure_modes stabilize` (¬ one-hop — premature termination is the dominant failure).
- `∀ early_bind: postpone until required_tables_exist` (L0 when run; both L1 ∧ L2 when solution-searching).
- `∀ detent≥wide ∧ objection∈{complexity_accrual, architecture_suitability}: recommended_bind ⊨ decorrelated_alt ∨ kill(keep_and_add)` — keep-and-add = rival-fill miss / SCOPE-DRIFT (¬ optimal L1); L1 patches under that objection do not close architecture suitability (friction a:27143).
- Compress last: deliver ranked tables + one recommended next bind with falsifiers. `¬ single-fork adjudication unless the packet closes the aperture`.

## Aperture detents (the parameter-shrink knob)

Detent = the one-line declarable aperture setting. IO/CoT/single-fork are limited-`b·T` special cases of the same tree search; "Opus = same machinery, narrower aperture" is a detent step-down, not a redesign.

| Detent | b·T | Entry criteria | Per-layer default | Cost |
|---|---|---|---|---|
| `closed` | b≈1–2, shallow T | ≤2 material sub-parts, or answer-space with loci pre-selected | **thin L2 fill required** (few **decorrelated** rival architectures/libraries/control flows + research gloss **as lexical spark**); shallow L1 optional; ¬ L0 re-open | cheapest |
| `standard` | b≈2–3, moderate T | bounded solution search, loci known | L1 required; **L2 required** (hypotheticals fill — not optional) | moderate |
| `wide` | b≈3–5, deep T | architecture suitability in scope; rival architectures/libraries/control flows live | L1 ∧ L2 both; L0 optional | high |
| `frontier` | widest b, deepest DFS | question-space unknown ∨ architecture challenge ∨ high-rework new territory | L0 (Max) → L1 ∧ L2 (High) | highest |

Close trigger (falsifier): `|material_sub_parts| ≤ 2 ⇒ step down to closed`.

**Closed-detent quick recipe (summary — single light consult, ¬ the bundled arc):** scope-lock (4 fields) → thin L2 fill (2–3 decorrelated rivals + research-anchor gloss) → single-seat dissent beat (one line, ¬ N-way debate) → bind with falsifier. Transport `team_dispatch(op=generate, seat=cursor-sdk, model=cursor/grok-4.5, contract=light-bounded, effort=low)`; preflight `manage(busy_status)` before firing. Sufficient iff `|material_sub_parts| ≤ 2` ∧ loci pre-selected ∧ ¬ architecture-suitability in scope — escalate to the bundled arc the moment a rival bind touches an invariant or the fix is not self-verifiable. Full recipe + friction-conveyor detent triage: **annex A**.

## Cascade header grammar (copy, don't re-derive)

Every path-sim turn opens with a header carrying these fields:

```
thread: <bus:id> · consult: <slug> · turn: <n> · layers: <L0|L1|L2 this turn>
detent: <closed|standard|wide|frontier> · effort: <declared> (declared ≠ verified)
deliverable gate: <what "answered" looks like>
```

`declared ≠ verified`: effort is not receiver-side introspectable. A mismatch is a present-tense nudge, never a blocking claim.

## The handshake packet (delivery — caller composes ~10 lines)

Primary delivery is an explicit slug line, not a paste. Caller composes:

1. **Scope-lock** (paste FIRST, after corpus) — 4 fields + reviewer rule:
   ```
   SCOPE-LOCK (conformance before merits)
   Question: <exact decision, operator's words — verbatim when operator-seeded>
   Out of scope: <the load-bearing negative>
   Good answer looks like: <deliverable shape, so "answered" is checkable>
   Origin: operator-seeded <verbatim seed> | agent-initiated
   Reviewer rule: FIRST output is a scope check — does the deliverable answer
   Question and stay inside Out-of-scope? Verdict: RATIFY | REVISE | SCOPE-DRIFT.
   On SCOPE-DRIFT, STOP and return — do not review off-scope content on its merits.
   ```
   Pin shapes: **solution pin** (Question names the decision → aperture over L1/L2) · **question-space pin** (Question is meta → run L0 first, may propose a tightened Question). ¬ free-float without a pin.
2. **Turn header** (grammar above).
3. **Slug line:** `Use the path-sim skill (layer=… detent=… effort=…)`.

## Substrate house rules (one line each — tables in annex C)

- **Quality ≠ transport.** “Opus 5 Max / High”, “Fable Max”, “Grok-4.5 High” name **effort**, not a dispatch path.
- **Anthropic family** (`decision:anthropic-family-dispatch-substrate`): `model=anthropic/*` via Stargate API is **PROHIBITED**; wide consult / R-admit ⇒ **web-anthropic CDP**; `cursor/claude-opus-*` acceptable when live codebase navigation is needed. ¬ unlock API via routine `cost_intent`.
- **xAI on the code lane** (friction 25081): **A** / coding-lane Grok judgment / closed-detent light consult ⇒ **`seat=cursor-sdk, model=cursor/grok-4.5, contract=light-bounded`**; bundled-arc **Q** defaults to **CDP Fable** (¬ Grok). `role=artisan, model=xai/grok-4.5` on a checkout-present coding consult is **PROHIBITED** (`xai/grok-4.5` stays OK for engineering axis-2 skeptic on specs/design; Grok is **PROHIBITED** for outbound prose).
- **Composer** = Stage-B implement only — never the A (L1+L2) leg. Detail: `consult-routing` § Anthropic-family substrate · § xAI coding-substrate · annex C.

## Dispatch cascade + R positions (essentials — mechanics in annex A)

```
recon → Q (lead CDP Fable L0) → A (cursor-sdk Grok L1+L2 + bind) →[halt] R-admit (lead CDP web-anthropic Opus, default-on)
  →[ADMIT] implement (Composer) → R-after (/work-item-review · cursor/grok-4.5, default-on) → closeout
```

- **Lead orchestrates, does not author.** ¬ in-seat L0/L1/L2 reasoning or hand-implement on `judgment_required` arcs unless the operator explicitly overrides.
- **Q∧A are coupled (P1).** Never A without a Q sidecar/verdict; never Q without a following A. An operator frame is **input to Q**, never a substitute (`q_skipped` retired). Absence of frame stamps ⇒ unframed ⇒ normal **CDP Fable Q** → Grok A; ¬ escalate to the human for a frame. **Closed-detent quick recipe** (above) stays Grok-only — that carve-out is ¬ the bundled arc.
- **Default seating (operator 2026-07-28):** Q = CDP Fable (explore width); A = `cursor/grok-4.5`; R-admit = Opus CDP — so Q and R are **not** the same seat. Downgrade Q to Grok only under closed detent or explicit operator skip.
- **R is one posture at two pins**, both default-on: **R-admit** (before implement · web-anthropic CDP Opus · lead-owned — a headless worker "running R" collapses to self-certify) and **R-after** (after ship · `cursor/grok-4.5` on cursor-sdk · checkout-native delivery critique). Verdict grammar `RATIFY | REVISE | SCOPE-DRIFT` / `ADMIT | ADMIT_WITH_AMENDMENTS | RATIFY_WITH_CONDITIONS | RETURN`.
- **R ≠ skeptic ≠ Gate-6.** ¬ stamp `recon_waived` / `skeptic_ratified` / lead self-ADMIT as a substitute for CDP R. Lead confidence that a bind is "mechanical" is **self-signal** — R exists to challenge that claim.
- **Skip is a closed set:** operator `check_requested=false`, or transport genuinely down (CDP for R-admit / cursor-sdk for R-after) — log `reason_code` on the review sidecar + open `friction(owner=agent_skill:path-sim)`. "Mechanical" / "simple bind" / "credits thin" / "A already RATIFY'd" are **forbidden** rationalizations.
- **Long-running ≠ stalled.** `completion_phase=running` ∧ `stall_stage=null` ⇒ keep polling in short slices across turns; ¬ abort, ¬ invent `cdp_unavailable`, ¬ advance on `turn_idle` or wall-clock alone.

## Auto-advance essentials (binding before each hop)

| Before | Require |
|---|---|
| → A | Q sidecar with verdict (ranked Q table, or `frame_verdict` + `frame_delta` when framed) |
| → R-admit | A sidecar with ranked L1 ∧ L2 + recommended bind; **Gate-2 densify closeout done** — todo `source_uri` = `cortex://notes/system/specs/{slug}.md`, `doc_validate` gates 6/8/9 PASS, non-empty `files_expected` + `acceptance_criteria`, `implement_ready` assertion citing current `spec_sha256:` |
| → Stage-B implement | R-admit sidecar citing a real **CDP harvest URI** (`archive_uri`, or `content_proof` after consumer fs-read + sha re-verify) + admit-class verdict, **or** closed-set skip evidence; `implement_ready_preflight(source_ref=todo:{slug}).admitted === true`; amended binds re-validated + `spec_sha256` refreshed; halt on a duplicate non-terminal `contract=implement` for the same `source_ref` |
| → R-after | Stage-B closeout present; spec + attrs current; lead fires `/work-item-review todo:{slug}` on `seat=cursor-sdk, model=cursor/grok-4.5` |
| → Closeout | R-after verdict sidecar (or skip evidence); REVISE applied or follow-up seeded; **docstring criticals=0**; event-instrumentation closeout one-liner when applicable |

`¬ fire Stage-B` when the R sidecar is lead-authored prose without CDP harvest (self-certify theater). Ordered Gate-2 steps, the `doc_template` freeform ban, Stage-B packet shape, and the full anti-pattern table live in **annex A**.

## Vision-align flag (G4)

∀ durable path-sim **Q / A / R** sidecar: emit a `VISION-ALIGN` block in the footer, alongside the conformance checklist. Block grammar + corpus + surface globs: **`cortex://notes/system/specs/vision-align-grammar.md`** (shared SoT).

```
VISION-ALIGN
verdict: opportunity | drift | none
pillar: <member of served pillars[].id> | thesis | n/a
note: <≤25 words; cite digest law/debt line when verdict ≠ none>
digest_map_sha256: <map_sha256> · stale: <true|false>
```

`none` = stack-compatible (still emit) · `drift` = names the fight + one corrective bind or friction · `opportunity` = a concrete next move, not a slogan. Verdict ≠ `none` ⇒ pull rubric from GET `/api/v1/doctrine/vision-digest`; escalate via `sot_uris[]` / full MAP read only at wide detent — R-admit `pillar_disposition:` machinery in **annex C**.

## Conformance checklist (6 binary, per path-sim turn)

1. Scope-lock present, Question pinned, before any merits content.
2. Layer(s) honored as declared in header (no L0 re-open; no undeclared layers).
3. Required tables exist BEFORE the bind (both L1 ∧ L2 when solution-searching).
4. Ranked dispositions present (`optimal/acceptable/kill`; `ask-now/defer/kill` at L0).
5. Converge-last ordering; detent + effort declared in header.
6. `VISION-ALIGN` block present (verdict ∈ {opportunity, drift, none}).

Logging: conformance block in the turn's durable sidecar (§0 pattern); `miss ⇒ friction(owner=agent_skill:path-sim)`. Checker: self per turn; reviewer-rule extension at Stage 3.

## Runtime/tool claim verification (standing given — no path-sim footer)

Verifying a **claimed** tool·service·runtime outcome is not a path-sim gate — it binds fleet-wide via `completion-provenance-discipline` (quote the concrete tool-response payload) and presence-discipline **P3** (evidence before any done-claim). Satisfy those; do **not** emit a ceremonial per-turn footer. `EVENTS-PROBE` was retired 2026-07-21; write-time event judgment lives in `event-instrumentation-discipline`. Detail: annex C.
