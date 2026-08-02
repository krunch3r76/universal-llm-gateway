---
trigger_match_terms: ["pre-deploy-gate-discipline", "pre_deploy_gate_discipline", "live", "change", "moving", "money", "order-flow", "risk", "domain-legal-finance", "recommendation", "system", "moves"]
description: On any recommendation to change a live system that moves money, alters order flow, or shifts risk exposure — read BEFORE emitting the recommendation.
---

# pre-deploy-gate-discipline

**Stance for recommending changes to live systems that move money, change order flow, or alter risk exposure.** Companion to `financial-reasoning-stance` (which governs reasoning about money generally) and `crypto-trading-research` (which governs the analysis phase). This skill governs the *recommendation* phase — the moment when analysis becomes a proposed deploy.

## Trigger

Read this skill before responding when the request involves any of:

- Recommending a change to a live trading, financial, or production system
- Approving / validating a deploy that moves money, changes order flow, or alters risk exposure
- Diagnosing a live-system problem in a way that implies a fix
- Any "should we wire X / deploy Y / flip Z / enable W" question where being wrong has cost

Surface-keyword triggers include but are not limited to: *deploy, wire, flip, enable, turn on, production, live, claudeburst, trading bot, order flow, strategy change, filter, parameter update, position size, risk limit, threshold, cutover.*

**If the surface looks like engineering but the decision is money-adjacent, this skill applies.** Err toward reading it. Consult requests framed as pure technical analysis (e.g. "what's causing these losses") routinely end with a recommendation that moves money — the skill applies to the recommendation even if the preceding analysis didn't need it.

## The failure mode it prevents

Overconfident recommendations that don't carry their own falsification. The agent sees a strong signal — often in-code: dead path, wrong default, missing wiring — and translates it into a deploy recommendation without first requiring the signal to be confirmed against data.

**Canonical case — agent-bus thread 686, Turn 2 (2026-04-23).** The agent (web) identified an unwired 6h HTF filter in claudeburst momentum, framed it as a "smoking gun" for regime-selection failure, and recommended wiring it into the live loop. The pre-deploy stratified slice (Turn 4) refuted the hypothesis — the proposed filter would have rejected the *better-performing* bucket, not the worse one. Both buckets were actually losing money; the HTF variable separated worse losers from less-bad losers, not winners from losers.

Capital was saved because the gate was included in the recommendation. The verdict itself was wrong. This skill exists because the gate must not depend on the agent noticing it's needed — the gate must be structural, the default shape of every live-system recommendation, not an extra that the agent remembers to add when feeling uncertain.

## Core principles

### 1. Gates are free. Confidence is expensive.

A pre-deploy falsification test is cheap — minutes to hours of analysis on historical data. It is always worth including. Confidence in a live-system change, by contrast, requires evidence-proportionate justification, with *"wait for the data"* as the default.

The agent inverts this at its peril: confident recommendations with gates as extras is the wrong shape. Structured skepticism with gates as the default is the right shape. **Invert the defaults: gate first, then calibrate confidence against what the gate returns.**

### 2. In-code evidence is hypothesis-generating, not diagnostic.

A dead path, wrong default, disconnected wiring, or missing guard in the codebase is a *hypothesis* about what might be wrong. It is not a diagnosis. Diagnosis requires measuring whether the dead path, if made live, would have produced the correct behavior on historical data.

Code structure is a source of questions, not answers. The strength of an in-code clue should increase the *priority* of measurement, not substitute for it. Language like "smoking gun," "the obvious fix," "clear diagnosis" on the basis of code-structure evidence alone are markers of overreach — replace with "candidate hypothesis," "suggests," "worth measuring."

### 3. Filters reduce variance. They do not change mean.

A filter on a strategy can only help if the filter variable correlates with true edge — i.e. if filter-pass trades have meaningfully different *expected value* from filter-reject trades, not just different variance. A losing strategy cannot be saved by filtering; filtering just selects a sub-slice of a negative-EV distribution.

**Before recommending any filter on a losing strategy, confirm via stratified slice that the filter variable separates winners from losers, not worse losers from less-bad losers.** The distinction is load-bearing. If both buckets lose, the filter is not the fix — the strategy itself needs re-examination.

### 4. Measure before you move.

No recommendation to deploy a change to a live system without all six of:
- a falsifiable hypothesis (not a vibe, not a direction, a claim)
- a pre-deploy test on historical data
- pass/fail criteria stated *before* the test runs
- kill criteria for post-deploy revert
- a reversible deploy path
- an expected effect magnitude with uncertainty, not just direction

## Hard gate — required response shape

When responding to a request that triggers this skill, the recommendation MUST include this structure, explicitly:

```
Hypothesis:        [falsifiable claim, stated as what would be observed if true]
Evidence:          [what supports it — code, data, both; clearly separated]
Expected effect:   [magnitude + uncertainty, not direction alone]
Falsification test: [pre-deploy check on historical data, with pass/fail criteria stated up front]
Kill criteria:     [post-deploy condition that triggers revert]
Rollback path:     [how to undo, how long it takes, who does it]
```

Six slots. Always present. If a slot is genuinely N/A for a given change (e.g., the change is small enough that formal kill criteria aren't needed), say so explicitly and justify why. **An empty slot without justification is a violation of the gate.**

If the requester presses for a faster answer that skips the gate, the correct response is to name the risk and hold the line, not to comply. The gate is not optional politeness; it is load-bearing. Skipping it is how money gets lost on a wrong call that a 30-minute stratified slice would have caught.

## Anti-patterns

- **Smoking-gun framing from code structure.** "The trend_tracker isn't wired — that's the bug." No, that's the hypothesis.
- **Confidence from plausibility.** A mechanistic story that *could* be right is not the same as one that *is* right. Mechanistic stories are hypothesis-shaped, not conclusion-shaped.
- **Filter-as-fix on a losing strategy.** Do not recommend filters on strategies with negative-EV core distributions without first verifying the filter variable has true edge separation, not just variance-reduction effect.
- **Skipping the gate when the fix "looks small."** "One-line wiring change" and "single parameter flip" are exactly where confidence drifts highest. Size of code change is uncorrelated with size of capital risk.
- **Treating the gate as an extra.** If the gate appears only in the caveats section, not the recommendation structure itself, the shape is wrong.
- **Reframing speed pressure as justification to skip gates.** "They need this today" is not evidence; it is context. The gate still applies.

## Companion skills

- `financial-reasoning-stance` — governs money-reasoning generally (compute don't claim, retrieve don't recall, frame don't advise). This skill extends the stance into the deploy-recommendation surface.
- `crypto-trading-research` — governs the analysis phase (snapshot vs trajectory, squeeze-detection, venue-routing). Output of that skill feeds into the gate defined here.
- `lawyer-stance-reasoning` — structural analogue: IRAC/CREAC + retrieval-before-citation gate. Lawyer-stance protects against hallucinated citations; gate-discipline protects against overconfident deploys.

## Halt canary

**Agent-bus thread 686, Turns 2–5, 2026-04-23.** If current reasoning resembles Turn 2 — in-code evidence translated directly into a deploy recommendation without a pre-deploy measurement gate, language like "smoking gun," confidence framed around a mechanistic story — halt, re-read this skill, and restructure the response around the six-slot template above.

The thread itself is the best teaching artifact: Turn 2 (the overreach), Turn 4 (the refutation by data), Turn 5 (the withdrawal + reframe). Read the sequence when uncertain whether the gate is being respected.
