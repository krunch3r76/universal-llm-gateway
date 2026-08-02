---
name: multi-model-review
description: "Multi-model adversarial review chain \u2014 GPT reviewer 1 then web-claude reviewer 2 with priors; panel roles, decisive_falsifier, A/B artifact."
---

# Multi-model review chain

## Trigger

Use when false-negative cost exceeds async wait cost, for cross-cutting changes, adversarial/dialectical review, or future `--chain` review mode.

Do **not** use for implement/bound-mechanical dispatch by default: producing Phase-1 review already covers it. Opt out for routine single-subsystem diffs, suggestion-only first passes, or `wait_cost > false_negative_cost`.

## Default consult rule

`substantive_reasoning_or_design_consult ∧ cross_cutting ⇒ recommended_review=cross-family-reconcile:default-on` unless explicit structured opt-out.

Default adversary = cross-family reviewer, default `openai/gpt-5.6-terra` (role default), rotatable. `openai/gpt-5.5` is **operator-gated** — not standing default. Material engineering lead decisions escalate to `panel_dispatch` / ≥2-family panel (Grok skeptic + Terra reviewer). **Writing / correspondence** uses Terra+Gemini, not default panel_dispatch — see `consult-routing` § Writing consult substrate.

Required default-on output item: `<negative_space>`.

`∀ new_or_changed(param ∪ branch): reviewer states invalid contexts ∧ verifies spec rejects them with a test`.

`accepted_and_forwarded ∧ consumed_on_one_lane_only ∧ silently_ignored_elsewhere ⇒ defect`, not no-op.

Track divergence with handoff override-audit events:
- high override/opt-out rate ⇒ default may be misfit;
- high post-reconcile finding delta ⇒ default earns cost.

## Panel-aligned chain

For consensus panel work, align with `panel_dispatch` / `team_dispatch` defaults:

| Role | Default family | Requirement |
|---|---|---|
| skeptic | Grok via `resolve_agent_model("skeptic")` — **engineering panels only** | emit `decisive_falsifier` |
| reviewer | GPT / `openai/gpt-5.6-terra` (role default); `gpt-5.5` only with operator auth | independent second family |
| synthesizer | Gemini | optional tiebreaker; **primary second family for writing** (with Terra reviewer) |

Lead adjudication after panel is **NON-offloadable**. Menu D assert uses `build_panel_assert_attributes` + `lead_adjudication_artifact`.

Panel-close assert fields: `consensus_disposition=panel`, `panel_families`, `panel_executions`, `lead_adjudication_artifact`, and ≥2 `execution:` evidence URIs.

## Workflow

1. Dispatch Reviewer 1 with `/session-review` or `/diff-review` packet; default `team_dispatch(op=generate, role=reviewer)`.
2. Dispatch Reviewer 2 with a clean packet or Reviewer 1 packet plus findings-only addendum:
   - include `Prior pass findings (do not assume correct)` + Reviewer 1 findings verbatim;
   - instruct: validate, refute, extend, surface false negatives, and mark high-confidence concurrence;
   - do **not** include Reviewer 1 evidence trail unless explicitly instructing re-derivation.
3. Synthesize:
   - both reviewers flag ⇒ high-confidence; apply by severity;
   - Reviewer 1 only ⇒ test false positive vs Reviewer 2 blind spot;
   - Reviewer 2 only ⇒ test adversarial false negative;
   - single-source findings receive normal triage.
4. Write A/B artifact: `tmp/reviews/<branch>-<command>-ab-summary.md`.

## Output contract

Every reviewer packet and lead Menu D assert includes `decisive_falsifier`: one sentence naming evidence that would refute the claim. Skeptic output must not be vibe-only disagreement.

```xml
<output_format>
  <findings>...</findings>
  <negative_space>Per new/changed param or branch: where invalid + spec rejection test. Accept-and-forward-but-consume-on-one-lane = defect.</negative_space>
  <decisive_falsifier>Single falsifiable observation that would overturn the claim.</decisive_falsifier>
  <severity_notes optional="true">...</severity_notes>
</output_format>
```

Reviewer 2 addendum:

```xml
<prior_pass_findings reviewer="gpt-5.6-terra" dispatch_id="<exec_id>">
<verbatim findings from Reviewer 1>
</prior_pass_findings>

Task:
1. Read live source independently; do not rely on Reviewer 1.
2. Validate/refute/extend each Reviewer 1 finding by FindingID.
3. Surface false negatives Reviewer 1 missed.
4. Mark high-confidence concurrence.
5. Treat disagreement on severity/applicability as signal.
```

## Anti-patterns

- One reviewer per concern/thread: do not pile chain mode onto one `agent_bus` thread; thread 1086 turn 3 showed this breaks one-concern-per-thread.
- `chain ≠ triage`; it adds recall, not pre-triaged findings.
- Do not run chain on suggestion-only first-pass results.
- Do not contaminate Reviewer 2 with Reviewer 1 evidence trail unless requiring independent re-derivation.
- Do not hard-code “gpt-5.5 then web-anthropic”; use cross-family pairs. Engineering panels: skeptic→Grok, reviewer→Terra. Writing: Terra+Gemini — ¬ Grok, ¬ unauthorized gpt-5.5.
- Do not omit `decisive_falsifier`; `panel_disposition_incomplete` flags incomplete panel stamps.

## Empirical basis / related

Basis: thread 1086; RAG `bitsai-cr-automated-review.pdf`; RAG `systematic-failures-code-verification.pdf`.

Related: `/session-review`, `/diff-review`, `todo:review-chain-mode-design`, `agent_skill:dispatch-workflow`, `agent_skill:implementation-plan-workflow`, `agent_skill:review-task-guidance`.
