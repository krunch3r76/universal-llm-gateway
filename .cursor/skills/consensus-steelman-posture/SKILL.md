---
name: consensus-steelman-posture
description: "On MATERIAL lead decisions — invariant changes, hard-to-reverse, legal/financial exposure, or close gates — steelman, seek consensus, document dissent."
---

# Consensus + Steelman Lead Posture

Authority: lead seats only (`web-anthropic`, `cursor-as-lead`). Companion: `frontier-reasoning-discipline`, `auditor-validatable-confidence`, `engagement-stance`, `lead-seat-boot`.

Core: material lead decisions require two cost tiers plus an auditable trace. `consensus_disposition` detects misses; it does not prevent in-turn forgetting. Session-close audit gate is the forcing function.

## Surface gate (life vs code)

Life MCP excludes CODE_EXTRA (`team_dispatch`, `panel_dispatch`, `pipeline`,
`manage`, `observability`). Cognitive legs in this skill run on every seat.
CODE_EXTRA call sites = **code MCP only**. On life/claude.ai: (1) run cognitive
legs in-seat; (2) `agent_bus` ask a code seat to fire the transport; or (3)
stamp honest `steelman-only` / operator bridge — ¬ call CODE_EXTRA from life.

## 1. Material gate

`material(decision) ⇔ hard_trigger ∨ paired_soft_trigger`.

Hard triggers (any ⇒ full posture + panel):
- policy / invariant change;
- many-row or hard-to-reverse mutation;
- deadline / legal / financial exposure.

Soft trigger: `competing_defensible_options ∧ (non_trivial_reversal_cost ∨ hard_trigger) ⇒ material`. Standalone competing-options ⇒ `steelman-only`, no panel. Else ⇒ `n/a-mechanical`.

Gate on structural facts, not self-rated confidence. Low confidence may raise a borderline case to panel; high confidence may never downgrade a hard trigger.

## 2. Required disciplines

### Steelman — unconditional

`material(decision) ⇒ steelman(each_live_option)` including the option you lean against.

**Adversarial independence (authorship stake).** A genuine adversarial / steelman-*against* leg (`KEEP|REJECT` attack on a recommendation) `⇒ different_provider_family ∧ ¬authorship_stake(critic, artifact)`. Asking the artifact's author to adversarially attack its own prior is steelman-theater — it does **not** satisfy adversarial independence (panel §8 already bans same-family self-dispatch; this rule covers same-*author* self-critique). Split the functions: original author DESIGN/reconcile (context); a different family with no authorship stake runs the adversarial KEEP/REJECT leg. Adversarial consults optimize for the optimal answer, not convenient-looking rigor. Precedent: friction 24217 / agent-bus:5092 (Fable "adversarial self-kill" of its own 5082 retire recommendation).

### Panel — hard triggers only

`hard_trigger ⇒ ≥2 distinct provider families + lead_adjudication`.

Rules:
- Independent family = distinct provider (`anthropic`, `openai`, `xai`, `google`). Same-provider variants do not count.
- Distinct families are a proxy for error-independence, not proof of independent errors. Authorship stake is a separate independence axis: critic ≠ author of the artifact under review (see Steelman adversarial-independence rule above).
- Output is not a vote tally. Adjudicate on decisive falsifier/evidence; record tally, falsifier, and divergence from operator prior.
- Skeptic verdict must cite a measurement/falsifier, not reasoned objection alone.
- Gemini is named tiebreaker; spend only on real split.
- **Code surface only:** use `panel_dispatch(disposition=panel, messages=..., dispatch_thread_id=...)` or manual `team_dispatch` skeptic+reviewer(+optional synthesizer). Poll `pipeline(result)` for panel executions unless helper response says otherwise. On life: steelman + disposition in-seat; `agent_bus` a code seat to fire transport, or stamp `steelman-only` / operator bridge.

### Ratification loop + conditions

`REJECT → revise → re-ratify` is the healthy path, ¬ a process failure: a round-1 `REJECT` naming a decisive falsifier is the gate catching a real defect **before** implementation. Revise the artifact against the falsifier and re-submit for a fresh verdict; ¬ re-dispatch-until-pass, ¬ log the REJECT as error.

Adjudicate-not-rubber-stamp extends to `RATIFY-WITH-CONDITIONS`: accept the reviewer's **diagnosis** while refining its exact **prescription** iff (1) the deviation is documented in BOTH the spec `reasoning_trace` and the ratifying assertion, AND (2) the reviewer's own motivating example / falsifier is re-verified to pass under the refined form. Tightening a condition because its literal wording would reopen the defect the prior round just closed is legitimate adjudication; silently swapping your own rule for the reviewer's is not. Precedent: `agent-bus:4798` turns 5→8 — a round-2 "substring-anywhere" path-block condition applied as a line-anchored leading-token rule (AC3c); deviation recorded in assertion 23406 + spec `reasoning_trace`; skeptic's motivating example re-verified.

### Role / op table

**Transport column = code surface only** (see Surface gate). Life seats run cognitive legs + disposition stamping; delegate CODE_EXTRA to a code seat.

| Situation | Transport | Role / target | Notes |
|---|---|---|---|
| Lead dialectic + adjudication | agent-bus + operator push | `web-anthropic` | NON-offloadable |
| Automated review w/o push | `team_dispatch(role=reviewer)` | gpt-5.6-terra | never gemini as reviewer; gpt-5.5 operator-gated |
| Adversarial panel member (engineering) | `team_dispatch` / `panel_dispatch` | skeptic → grok | falsifier required; ¬ writing |
| Read/RAG / writing second family | `team_dispatch(role=synthesizer)` | gemini | writing: Terra+Gemini, not default panel |
| ≥2-family engineering panel | `panel_dispatch` | skeptic + reviewer | lead artifact required after |
| Writing / correspondence multi-model | reviewer + synthesizer | terra + gemini | ¬ grok; ¬ gpt-5.5 without operator auth (`consult-routing` § Writing) |
| One-shot provider consult | `team_dispatch(op=generate, role=artisan, model="<provider/model>", contract="light-bounded", mcp=False, dispatch_thread_id="<thread>")` | chosen model | inline; on-behalf delivery |

## 3. Three panel guards

1. **Capability binds to effective model, not role label.** Admission enforces effective-model tool surface: gemini-family inline-only on any role; `reviewer + model=gemini` gets no tools. Non-multi-agent grok gets MCP; xAI multi-agent models are inline.
2. **Offload boundary is artifacted.** Offloadable: sidecar/RAG/code legwork and running panel members. NON-offloadable: live-options steelman, decisive-falsifier adjudication, and lead review of panelist Cortex writes. A material `panel` decision requires a lead-authored adjudication artifact after panel results and before close containing live-options steelman, accepted/rejected falsifier, family tally, and explicit review of panelist writes. Absent artifact ⇒ honest stamp is `steelman-only`, not `panel`.
3. **Audit-semantic binding.** `consensus_disposition`, `material`, `panel`, `panel_families`, `decisive_falsifier` are schema-bound and machine-checkable. Ritual without semantic content is failure.

Transport unification only enables lower-friction audited panels if Guard 2 artifact captures lead cognition; otherwise it encourages black-box answer consumption.

## 4. Auditable trace

### `consensus_disposition` on material `decision:*` assertions

| Value | Meaning |
|---|---|
| `panel` | ≥2-provider vote ran; tally + decisive falsifier recorded; lead adjudication artifact exists |
| `waived-by-operator` | reminder named; operator overrode; waiver text in evidence |
| `steelman-only` | steelman ran; panel not warranted, or panel lacked artifact |
| `n/a-mechanical` | not material |

Source of truth = append-only assertion attributes. Entity attributes may mirror latest state as cache only; audits query non-superseded assertions.

`panel` required fields:
- `panel_families`: ≥2 distinct providers;
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
    "panel_families": ["Anthropic", "GPT", "Grok"],
    "panel_executions": {"skeptic": "<exec_id>", "reviewer": "<exec_id>"},
    "decisive_falsifier": "<single computable measurement>",
    "panel_adjudication_artifact": "<uri or bus turn>",
    "material": true
  },
  "evidence_uris": ["agent-bus:<thread>", "execution:<exec_id>", "execution:<exec_id>"]
}
```

### Falsifier counter

Over `N ≥ 20` material decisions at held volume, posture health is falsified if the fraction of `consensus_disposition=panel` decisions lacking lead-authored adjudication artifact rises. Diagnostic only: decisive-falsifier origination-or-adoption must appear in lead context/adjudication; do not require verbatim pre-dispatch substring.

Never fabricate provenance. Stamp what actually ran; never auto-stamp `panel` for one-shot, one-family, missing-artifact, or skipped-panel cases.

### Session-close gate

Session close must warn/block clean close when material-decision activity exists and material `decision:*` assertions lack `consensus_disposition`. Standalone cadence queries are insufficient because undispositioned decisions may never get assertions.

### Cadence

Review every 10 logged material decisions or monthly, whichever first. Inputs: waive-rate (high ⇒ tighten gate) and panel-overturn-rate (≈0 ⇒ panel may not earn cost). Tracked by `todo:consensus-steelman-lead-posture`.

**Autonomous work-item spine sample (C4 — `decision:autonomous-work-item-spine`):** in addition to the material-decision cadence above, run a sampled conformance/drift audit every **5** autonomous spine closes **or** at each standing-root CHECKPOINT, whichever first. Scope (B5): `decision:anthropic-api-model-operator-gated` / a23426 honored, spine invariant conformance, process drift, check-pass quality. **Out of scope:** re-run anchors / pytest / skeptic. Prefer manual web or CHECKPOINT-batch (not mid-pipeline). Ratify-before-codify remains a hard gate on every fleet-codification event (consult-routing § Ratify-before-codify).

## 5. Governance: panelist Cortex writes

Panelist Cortex writes are inputs, not adjudicated results. Lead must review them before relying on them; worked example: gemini assertion 12778 was wrong and was superseded by lead as 12778→12791. Transport changed, governance did not: `team_dispatch`/`panel_dispatch` results remain lead-adjudicated inputs.

## 6. Boot framing

From `decision:boot-identity-by-allusion`:
- Identity by practice, not injection. Endpoint provenance carries who-did-what; no “you are Claude” preamble. Change A rejected.
- Constitution by allusion, not re-feeding. Ranking: allusion > nudge > instruction > injection.
- Beacon was cited backwards: direct read argues against shallow prompt preambles. Allusion is lowest-risk prompt-level option but efficacy is unproven and beacon-doubtful. Default: lean boot + existing structural allusion.
- Any added evocation requires blinded A/B: behavior-only boot vs +evocation on pushback / false-premise / write-confirm tasks. Score = correct-pushback + calibration minus sycophancy/unconfirmed-write penalties; zero tone credit; reject on any significant execution regression. Pass bar: ≥5–8pp or ≥0.15 SD, CI>0, p<0.05, no calibration/execution regression.
- Change B may proceed on operator confirm: register-only reframe away from “Non-Negotiable”; preserve behavior; renderer-source home.

## 7. Sequencing / implementation state

- Phase 0 landed: this skill patch, role/op table, assert template, split storage shape.
- Phase 0/1 landed: effective-model capability binding; gemini inline-only under overrides; stale xAI blanket flatten removed; tests in `libs/agent_seat/test_hydration.py` and frontier consult tests.
- Phase 1 passed: gpt-5.5 MCP probe (`execution` eaf07f01).
- Phase 2 landed: `panel_dispatch` helper returns executions/families; lead artifact remains non-offloadable.
- Phase 3: cadence + decisive-falsifier counter.

## 8. Non-code lane (personal / legal / financial decisions)
Same posture, personal-decision adaptation. Source: `decision:noncode-independent-challenge-mechanism` (consult 4466); disposition OPEN pending the mechanism's own cross-family exam. Full proposal: `cortex://notes/system/threads/noncode-independent-challenge-proposal.md`.

**Trigger (adapts §1 to personal matters).** Gate on structural facts, never on felt confidence. Hard triggers (any one ⇒ offer a 2-family challenge): hard-to-reverse commitment (sign lease/contract/settlement, accept/quit job, relocate, medical election, file/respond in a legal matter, marriage/divorce/custody); material legal/financial exposure (operator-tunable floor, e.g. > 1 month's income or > 5% liquid assets, or any limitations-bound act); deadline-bound irreversibility; social/identity irreversibility (public commitment, family disclosure, ending a relationship). Soft trigger: competing defensible options ∧ non-trivial reversal cost ⇒ one cross-family skeptic. Amplifier: acute emotional load (anger-quit, grief-sale, fear-settle) lowers the bar one notch — never a trigger alone.

**Shape — parallel · blind · bounded · single-shot · falsifier-oriented.** Pool nothing, debate nothing (judging-many-minds: a 2-member vote-pool is the *worst* config; persuasion-overrides-truth + boundary-sync: debate and chatter homogenize). Lead writes its steelman of every live option to the thread FIRST (also the §7-falsifier baseline). Challengers never see each other; zero challenger-to-challenger comms. Each returns: strongest case against each live option (incl. the unstated leaning), **one checkable fact that would change the answer**, and what's missing from the brief. Lead adjudicates on the decisive checkable fact — not a vote.

**Roster — reuse domain-general roles, mint nothing.** Soft ⇒ `skeptic` (grok). Hard ⇒ `skeptic` (grok) + `reviewer` (gpt-5.5), parallel and blind. Real split ⇒ `synthesizer` (gemini) as named tiebreaker only. Independence is inherited (`is_independent`, family/version granularity); same-family self-dispatch (e.g. web-anthropic self-dispatching Anthropic) never counts; same-author self-adversarial (critic = author of the recommendation under review) also never counts — see §2. What differs from the code lane is the *briefing* (challenge-the-decision, not review-the-diff), carried in the dispatch turn.

**`mcp=False` always.** Personal/legal/financial context must not gain tool surfaces, and a challenger reading the lead's notes couples to the lead's framing — so the redacted brief is the *only* channel.

**Redaction floor (R1 — RULED, operator-delegated, operator-vetoable).** `cortex://notes/system/threads/4466-r1-redaction-floor-ruling.md`. Briefs leaving the Anthropic surface carry decision **structure** only — options, constraints, stakes as ratios/coarse buckets, deadline as a relative window, the checkable facts at issue. **Strip** by default: the person's and third parties' names (counterparties by role — landlord/employer/ex-spouse — never by name), contact info/addresses/employers, account numbers, and exact identifying amounts where a ratio/bucket carries the same weight. **Never** send documents verbatim or as images — extract the specific clause/fact in your own words (the code lane's evidence-not-apparatus wall). Absolute figures/verbatim clauses cross only when the checkable fact genuinely turns on them, then minimized.

**Invocation.** Manual: one line ("challenge this" / "red-team this") mid-conversation, from a phone — the lead composes brief + steelman, shows the human the exact redacted brief for a one-glance consent ("sending this to GPT + Grok — ok?"), then fans out; the human authors nothing. Auto-**offer** on a detected hard trigger, never auto-fan-out — personal context leaving the surface needs per-invocation consent.

**Transport (surface-split).** Cognitive legs (brief, steelman, consent, adjudication, `consensus_disposition` stamp) run on **every seat including life**. Fan-out to skeptics/reviewers is **code surface only** — `team_dispatch(op=generate, role=skeptic|reviewer, contract=light-bounded, mcp=False, dispatch_thread_id=…)` or `panel_dispatch` for the two-member one-call. **On life/claude.ai:** after consent, `agent_bus` ask a **code seat** to fire transport with the redacted brief — do **not** call `team_dispatch`/`panel_dispatch` from life. If no code seat is available, stamp honest `steelman-only` or offer an operator bridge to a code-capable surface. Trace = the single `consensus_disposition` stamp per §4, with the falsifier and execution ids when transport actually ran.

**Wall — what must NOT cross from the code lane.** Banned: six-block XML packets, dense specs / `validate_dense_spec` / Gate-2, `files_expected`/`acceptance_criteria`, todo/plan promotion, `contract=implement`/`wrap`, cursor-sdk or any repo substrate, `auto_review_child` diff review, quality gates, multi-round review chains, challenger MCP access to the personal corpus. A challenger here mutates nothing, so the mutation-governing apparatus is out of scope by construction.

**Decisive falsifier (pilot: `todo:noncode-challenge-pilot`).** Over N ≥ 10 challenged non-code decisions, `challenge_delta` = did adjudication adopt ≥ 1 consideration/checkable fact absent from the lead's pre-dispatch steelman. If `challenge_delta = 0` in ≥ 8/10 ⇒ homogenization theater over inline steelman — retire. Secondary: if the two challengers' checkable facts near-duplicate in ≥ 8/10 ⇒ drop to single-skeptic permanently.

## Anti-patterns

| Bad | Good |
|---|---|
| Panel every competing-options call | Standalone soft trigger ⇒ steelman-only |
| Stamp `panel` because one-shot ran | `panel` requires ≥2 families + falsifier + artifact |
| Treat disposition as preventing misses | It detects; session-close gate forces |
| Claim panel errors are independent | State distinct-family proxy only |
| Ask author to adversarially attack its own prior | Route KEEP/REJECT leg to a different family with no authorship stake |
| Auto-trust panelist Cortex write | Lead-adjudicate it |
| Inject “you are Claude” at boot | Identity by endpoint provenance/allusion |
| Add warm evocation because it feels right | Require regress-guarded blinded A/B |

## Not in scope

Every-turn reasoning posture (`frontier-reasoning-discipline`), write-side confidence/audit mechanics (`auditor-validatable-confidence`), dispatch transport mechanics (`handoff-dispatchers.mdc`, `dispatch-workflow`), anti-agreement response shape (`engagement-stance`).
