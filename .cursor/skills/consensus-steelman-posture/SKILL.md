---
name: consensus-steelman-posture
description: "On MATERIAL lead decisions — invariant changes, hard-to-reverse, legal/financial exposure, or close gates — steelman, seek consensus, document dissent."
---

# Consensus + Steelman Lead Posture

**SOT for:** the material-decision gate (§1), the non-offloadable panel adjudication duties (§2), and the `consensus_disposition` audit schema (§3). Authority: lead / adjudicating-caller seats.

Core: material lead decisions require steelman plus — on hard triggers — a ≥2 distinct-`ConsultantIdentity`-pair panel and an auditable trace. `consensus_disposition` **detects** misses; it does not prevent in-turn forgetting. The session-close gate is the forcing function.

**Defers — do not restate here:**

| Concern | Owner |
|---|---|
| Binder/escalation independence (weight class ∨ family), when to escalate at all | `dispatch-kernel_ulg.mdc` § Ladder + binder order; anti-patterns on `consult-routing` skill § Judgment escalation ladder |
| Skeptic dispatch mechanics, MCP on/off path, `FILE_EVIDENCE_PATHS` footer, recon ladder | `cheap-recon-before-escalation` § Axis 2 |
| Every-turn steelman / calibration / courage / one-determinate-step | `reasoning-posture` |
| Posture before transport on an operator consult | `consult-posture` |
| Transport shapes, roster models, life-vs-code surface gate, writing lane | `consult-routing` · `panel_dispatch` descriptor |
| Write-side confidence mechanics · anti-agreement response shape | `auditor-validatable-confidence` · `engagement-stance` |

## 1. Material gate

`material(decision) ⇔ hard_trigger ∨ paired_soft_trigger`.

Hard triggers (any ⇒ full posture + panel):
- policy / invariant change;
- many-row or hard-to-reverse mutation;
- deadline / legal / financial exposure.

Soft trigger: `competing_defensible_options ∧ (non_trivial_reversal_cost ∨ hard_trigger) ⇒ material`. Standalone competing-options ⇒ `steelman-only`, no panel. Else ⇒ `n/a-mechanical`.

Gate on structural facts, not self-rated confidence. Low confidence may raise a borderline case to panel; high confidence may never downgrade a hard trigger.

## 2. Panel discipline

`material(decision) ⇒ steelman(each_live_option)`, including the option you lean against. Steelman craft itself is `reasoning-posture`; what binds *here* is that the live-options steelman is lead-authored and non-offloadable (Guard 2) — its absence downgrades the honest stamp to `steelman-only`.

`hard_trigger ⇒ ≥2 distinct ConsultantIdentity pairs + lead adjudication`.

- **Identity+rung rule (schema).** Independent panel member = distinct `ConsultantIdentity(model_identity, effort_rung)` per R-PANEL (`panel_dispatch.py` Guard 3). Same folded identity at a **different effort rung** counts toward panel diversity; same identity **and** same rung does not. This governs panel composition only — general binder independence is the escalation ladder's.
- **Proxy, not proof.** Distinct identity+rung pairs are a proxy for error-independence, not evidence that the errors were independent. State it as a proxy.
- **Authorship stake is a second independence axis.** `adversarial_leg ⇒ independently_measured(critic_identity, author_identity) ∧ ¬authorship_stake(critic, artifact)`. The identity+rung conjunct is the panel rule above; the authorship conjunct is additional — clearing identity+rung independence does **not** discharge it. Asking an artifact's author to adversarially attack its own prior is steelman-theater. Split the functions: the author does DESIGN/reconcile, and a critic with no authorship stake runs the `KEEP|REJECT` leg. Precedent: friction 24217 / agent-bus:5092 (Fable "adversarial self-kill" of its own 5082 retire recommendation).
- **Adjudicate, do not tally.** Panel output is not a vote. Decide on the decisive falsifier/evidence; record the tally, the falsifier, and any divergence from the operator prior.
- **Transport.** `panel_dispatch(disposition=panel, messages=…, dispatch_thread_id=…)` — roster, per-member capability, and knob resolution are in that tool's descriptor. Life seats run the cognitive legs in-seat and `agent_bus` a code seat to fire transport; with no code seat, stamp honest `steelman-only` or bridge via the operator.

### Ratification loop + conditions

`REJECT → revise → re-ratify` is the healthy path, ¬ a process failure: a round-1 `REJECT` naming a decisive falsifier is the gate catching a real defect **before** implementation. Revise the artifact against the falsifier and re-submit for a fresh verdict; ¬ re-dispatch-until-pass, ¬ log the REJECT as error.

Adjudicate-not-rubber-stamp extends to `RATIFY-WITH-CONDITIONS`: accept the reviewer's **diagnosis** while refining its exact **prescription** iff (1) the deviation is documented in BOTH the spec `reasoning_trace` and the ratifying assertion, AND (2) the reviewer's own motivating example / falsifier is re-verified to pass under the refined form. Tightening a condition because its literal wording would reopen the defect the prior round just closed is legitimate adjudication; silently swapping your own rule for the reviewer's is not. Precedent: `agent-bus:4798` turns 5→8 — a round-2 "substring-anywhere" path-block condition applied as a line-anchored leading-token rule (AC3c); deviation recorded in assertion 23406 + spec `reasoning_trace`; skeptic's motivating example re-verified.

### Three panel guards

1. **Capability binds to effective model, not role label.** Admission enforces the effective model's tool surface regardless of the role it was dispatched under — `reviewer + model=gemini` gets no tools. Per-member truth is returned as `panel_capabilities` (`inline_only`, `tool_surface`, `resolved_model`); read it rather than inferring capability from the role.
2. **Offload boundary is artifacted.** Offloadable: sidecar/RAG/code legwork and running panel members. NON-offloadable: live-options steelman, decisive-falsifier adjudication, and lead review of panelist Cortex writes. A material `panel` decision requires a lead-authored adjudication artifact after panel results and before close containing live-options steelman, accepted/rejected falsifier, identity+rung tally, and explicit review of panelist writes. Absent artifact ⇒ honest stamp is `steelman-only`, not `panel`.
3. **Audit-semantic binding.** `consensus_disposition`, `material`, `panel`, `panel_families`, `decisive_falsifier` are schema-bound and machine-checkable. `panel_families` is a display/cache of identity+rung labels; the gate tallies distinct `ConsultantIdentity` pairs. Ritual without semantic content is failure.

Transport unification only buys lower-friction audited panels if the Guard 2 artifact captures lead cognition; otherwise it just encourages black-box answer consumption.

## 3. Auditable trace

### 3.1 `consensus_disposition` on material `decision:*` assertions

**Assertion attributes are the source of truth.** Audits and the session-close detector query the non-superseded assertion, never the entity blob. `entity_update(attributes=…)` may mirror latest state as a derived read cache only — never as the primary write.

| Value | Meaning |
|---|---|
| `panel` | ≥2 distinct identity+rung pairs ran; tally + decisive falsifier recorded; lead adjudication artifact exists |
| `waived-by-operator` | reminder named; operator overrode; waiver text in evidence |
| `steelman-only` | steelman ran; panel not warranted, or panel lacked artifact |
| `n/a-mechanical` | not material |

`panel` required fields:
- `panel_families`: ≥2 distinct `ConsultantIdentity` pairs (identity+rung labels for display);
- `panel_executions`: role→execution_id;
- `decisive_falsifier`: non-empty, computable;
- `panel_adjudication_artifact`: uri/turn of post-panel pre-close lead artifact;
- `evidence_uris`: `agent-bus:T` + ≥2 `execution:E`.

Missing any field ⇒ stamp `steelman-only`. New writes use `panel_adjudication_artifact`; historical read alias `lead_adjudication_artifact` remains accepted.

Assert shape:
```json
{
  "entity_id": "decision:<slug>",
  "claim": "<verdict>",
  "derivation_type": "inference",
  "confidence": "believed",
  "attributes": {
    "consensus_disposition": "panel",
    "panel_families": ["claude-opus-4-7@high", "gpt-5.6-terra@high", "grok-4.6@high"],
    "panel_executions": {"skeptic": "<exec_id>", "reviewer": "<exec_id>"},
    "decisive_falsifier": "<single computable measurement>",
    "panel_adjudication_artifact": "<uri or bus turn>",
    "material": true
  },
  "evidence_uris": ["agent-bus:<thread>", "execution:<exec_id>", "execution:<exec_id>"]
}
```

Never fabricate provenance. Stamp what actually ran; never auto-stamp `panel` for one-shot, one-identity-pair, missing-artifact, or skipped-panel cases.

### 3.2 Session-close gate

Session close must warn/block clean close when material-decision activity exists and material `decision:*` assertions lack `consensus_disposition`. Standalone cadence queries are insufficient because undispositioned decisions may never get assertions.

### 3.3 Cadence + falsifier counter

Review every 10 logged material decisions or monthly, whichever first. Inputs: waive-rate (high ⇒ tighten gate) and panel-overturn-rate (≈0 ⇒ panel may not earn cost). Tracked by `todo:consensus-steelman-lead-posture`.

**Falsifier counter.** Over `N ≥ 20` material decisions at held volume, posture health is falsified if the fraction of `consensus_disposition=panel` decisions lacking a lead-authored adjudication artifact rises. Diagnostic only: decisive-falsifier origination-or-adoption must appear in lead context/adjudication; do not require a verbatim pre-dispatch substring.

**Autonomous work-item spine sample (C4 — `decision:autonomous-work-item-spine`):** in addition to the material-decision cadence above, run a sampled conformance/drift audit every **5** autonomous spine closes **or** at each standing-root CHECKPOINT, whichever first. Scope (B5): `decision:anthropic-api-model-operator-gated` / a23426 honored, spine invariant conformance, process drift, check-pass quality. **Out of scope:** re-run anchors / pytest / skeptic. Prefer manual web or CHECKPOINT-batch (not mid-pipeline). Ratify-before-codify remains a hard gate on every fleet-codification event (`consult-routing` § Ratify-before-codify).

## 4. Governance: panelist Cortex writes

Panelist Cortex writes are inputs, not adjudicated results. The adjudicating lead must review them before relying on them; worked example: gemini assertion 12778 was wrong and was superseded by lead as 12778→12791. Transport changed, governance did not: `team_dispatch` / `panel_dispatch` results remain lead-adjudicated inputs.

## 5. Non-code lane (personal / legal / financial decisions)

Same posture, personal-decision adaptation. Source: `decision:noncode-independent-challenge-mechanism` (consult 4466); disposition OPEN pending the mechanism's own cross-family exam. Full proposal: `cortex://notes/system/threads/noncode-independent-challenge-proposal.md`.

**Trigger (adapts §1 to personal matters).** Gate on structural facts, never on felt confidence. Hard triggers (any one ⇒ offer a ≥2 distinct-identity-pair challenge): hard-to-reverse commitment (sign lease/contract/settlement, accept/quit job, relocate, medical election, file/respond in a legal matter, marriage/divorce/custody); material legal/financial exposure (operator-tunable floor, e.g. > 1 month's income or > 5% liquid assets, or any limitations-bound act); deadline-bound irreversibility; social/identity irreversibility (public commitment, family disclosure, ending a relationship). Soft trigger: competing defensible options ∧ non-trivial reversal cost ⇒ one cross-identity skeptic (identity+rung independence per §2). Amplifier: acute emotional load (anger-quit, grief-sale, fear-settle) lowers the bar one notch — never a trigger alone.

**Shape — parallel · blind · bounded · single-shot · falsifier-oriented.** Pool nothing, debate nothing (judging-many-minds: a 2-member vote-pool is the *worst* config; persuasion-overrides-truth + boundary-sync: debate and chatter homogenize). Lead writes its steelman of every live option to the thread FIRST (also the falsifier baseline for §3.3). Challengers never see each other; zero challenger-to-challenger comms. Each returns: strongest case against each live option (incl. the unstated leaning), **one checkable fact that would change the answer**, and what's missing from the brief. Lead adjudicates on the decisive checkable fact — not a vote.

**Roster — reuse domain-general roles, mint nothing.** Soft ⇒ `skeptic` alone. Hard ⇒ the default two-member roster, parallel and blind. Real split ⇒ `synthesizer` as named tiebreaker only. Models come from the `panel_dispatch` roster — do not pin them here. Independence is inherited (`independently_measured`, `(model_identity, effort_rung)` folding) and both axes of §2 apply unchanged: same-identity-same-rung self-dispatch never counts, and neither does same-author self-adversarial. What differs from the code lane is the *briefing* — challenge-the-decision, not review-the-diff — carried in the dispatch turn.

**`mcp=False` always.** Personal/legal/financial context must not gain tool surfaces, and a challenger reading the lead's notes couples to the lead's framing — so the redacted brief is the *only* channel.

**Redaction floor (R1 — RULED, operator-delegated, operator-vetoable).** `cortex://notes/system/threads/4466-r1-redaction-floor-ruling.md`. Briefs leaving the Anthropic surface carry decision **structure** only — options, constraints, stakes as ratios/coarse buckets, deadline as a relative window, the checkable facts at issue. **Strip** by default: the person's and third parties' names (counterparties by role — landlord/employer/ex-spouse — never by name), contact info/addresses/employers, account numbers, and exact identifying amounts where a ratio/bucket carries the same weight. **Never** send documents verbatim or as images — extract the specific clause/fact in your own words (the code lane's evidence-not-apparatus wall). Absolute figures/verbatim clauses cross only when the checkable fact genuinely turns on them, then minimized.

**Invocation.** Manual: one line ("challenge this" / "red-team this") mid-conversation, from a phone — the lead composes brief + steelman, shows the human the exact redacted brief for a one-glance consent ("sending this to GPT + Grok — ok?"), then fans out; the human authors nothing. Auto-**offer** on a detected hard trigger, never auto-fan-out — personal context leaving the surface needs per-invocation consent.

**Transport (surface-split).** Cognitive legs — brief, steelman, consent, adjudication, `consensus_disposition` stamp — run on **every seat including life**. Fan-out to skeptics/reviewers is **code surface only**: `panel_dispatch` for the two-member one-call, or `team_dispatch(op=generate, role=skeptic|reviewer, contract=light-bounded, mcp=False, dispatch_thread_id=…)`. From life, `agent_bus` a code seat to fire transport with the redacted brief. No code seat available ⇒ stamp honest `steelman-only` or offer an operator bridge. Trace = the single `consensus_disposition` stamp per §3, with the falsifier and execution ids when transport actually ran.

**Wall — what must NOT cross from the code lane.** Banned: six-block XML packets, dense specs / `validate_dense_spec` / Gate-2, `files_expected`/`acceptance_criteria`, todo/plan promotion, `contract=implement`/`wrap`, cursor-sdk or any repo substrate, `auto_review_child` diff review, quality gates, multi-round review chains, challenger MCP access to the personal corpus. A challenger here mutates nothing, so the mutation-governing apparatus is out of scope by construction.

**Decisive falsifier (pilot: `todo:noncode-challenge-pilot`).** Over N ≥ 10 challenged non-code decisions, `challenge_delta` = did adjudication adopt ≥ 1 consideration/checkable fact absent from the lead's pre-dispatch steelman. If `challenge_delta = 0` in ≥ 8/10 ⇒ homogenization theater over inline steelman — retire. Secondary: if the two challengers' checkable facts near-duplicate in ≥ 8/10 ⇒ drop to single-skeptic permanently.

## Anti-patterns

| Bad | Good |
|---|---|
| Panel every competing-options call | Standalone soft trigger ⇒ steelman-only |
| Stamp `panel` because one-shot ran | `panel` requires ≥2 distinct identity+rung pairs + falsifier + artifact |
| Treat disposition as preventing misses | It detects; session-close gate forces |
| Claim panel errors are independent | State distinct-identity-pair proxy only |
| Ask author to adversarially attack its own prior | Route the `KEEP|REJECT` leg to a critic with no authorship stake |
| Write disposition to the entity blob | Assertion attributes are SOT; entity is read cache at most |
| Auto-trust panelist Cortex write | Lead-adjudicate it |
| Restate roster models / transport shapes here | Cite `panel_dispatch` + `consult-routing` |
