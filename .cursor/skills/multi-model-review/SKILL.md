---
description: Multi-model adversarial review chain — GPT reviewer 1 then web-claude reviewer 2 with priors; panel roles, decisive_falsifier, A/B artifact.
---

# Multi-model review chain

## When to use

- High-stakes review where false-negative cost > async wait cost
- Sessions involving cross-cutting changes (event vocabulary, transport, contract surfaces)
- Adversarial / dialectical review (one reviewer's blind spots are another's strengths)
- Foundation for --chain mode (todo:review-chain-mode-design)

## When NOT to use

- Routine single-subsystem diff reviews — single reviewer suffices
- Suggestion-only first-pass results — doesn't warrant async wait
- Sessions where cost-of-wait > false-negative cost

## Panel-aligned chain roles (Menu E / thread 1206)

When the review is a **consensus panel** (not a casual two-pass diff review), align
roles with `agent_seat.panel_dispatch` / `team_dispatch` defaults:

| Role | Default model family | Notes |
|---|---|---|
| **skeptic** | Grok (`resolve_agent_model("skeptic")`) | Must surface a **decisive_falsifier** — what observation would refute the claim |
| **reviewer** | GPT (`openai/gpt-5.5` or role default) | Independent second provider family |
| **synthesizer** (optional) | Gemini | Tiebreaker only when skeptic + reviewer disagree; not a third mandatory pass |

Lead adjudication after panel runs is **NON-offloadable**; Menu D assert uses
`build_panel_assert_attributes` + `lead_adjudication_artifact`.

## Workflow shape

1. **Reviewer 1 dispatch**: build packet per /session-review or /diff-review; dispatch to first reviewer (default `team_dispatch(op=generate, role=reviewer)`). Capture findings inline (synchronous) or via thread fetch (async).
2. **Reviewer 2 dispatch**: rebuild packet OR re-use Reviewer 1's packet plus an addendum:
   - addendum block: 'Prior pass findings (do not assume correct):' followed by Reviewer 1's findings verbatim
   - addendum instruction: 'Validate, refute, or extend; flag false negatives — issues Reviewer 1 missed; concur with high-confidence agreements'
   - dispatch to second reviewer (typically web-claude for MCP-grounded adversarial pass)
3. **Cursor synthesis (comparison pass)**:
   - Agreements (both reviewers flagged): high-confidence; apply directly per severity rules
   - Reviewer 1 only: validate against Reviewer 2's silence — false positive or blind spot?
   - Reviewer 2 only: validate against Reviewer 1's silence — false negative caught by adversarial pass?
   - Single-source findings get same triage as if from one reviewer
4. **Artifact**: write A/B comparison artifact (template: tmp/reviews/<branch>-<command>-ab-summary.md per existing /diff-review and /session-review escalation paths)

## output_format (panel / adversarial chain)

Every reviewer packet (and the lead's Menu D assert) should include an explicit
**decisive_falsifier** field — one sentence stating what evidence would refute the
under review claim if observed. Skeptic output must not be vibe-only disagreement.

```xml
<output_format>
  <findings>...</findings>
  <decisive_falsifier>Single falsifiable observation that would overturn the claim.</decisive_falsifier>
  <severity_notes optional="true">...</severity_notes>
</output_format>
```

For panel disposition closes, mirror `panel_dispatch` stamp fields on the
decision assert: `consensus_disposition=panel`, `panel_families`, `panel_executions`,
`lead_adjudication_artifact`, plus ≥2 `execution:` URIs on the backing assert.

## Prompt addendum template (Reviewer 2)

```
<prior_pass_findings reviewer='gpt-5.5' dispatch_id='<exec_id>'>
<verbatim findings from Reviewer 1>
</prior_pass_findings>

Reviewer 1 has already produced the findings above. Your task:
1. Read live source files independently — do not rely on Reviewer 1's interpretation
2. Validate, refute, or extend each Reviewer 1 finding (cite the FindingID)
3. Surface false negatives — issues Reviewer 1 missed. These are the highest-value contributions.
4. Mark concurrence on high-confidence agreements (so cursor's synthesis can stratify)
5. Do NOT defer to Reviewer 1's framing — disagreement on severity or applicability is signal, not failure
```

## Anti-patterns

- Piling chain mode onto a single agent_bus thread — 1086 turn 3 demonstrated this breaks one-concern-per-thread (Signal 5 from 1086). Open a separate thread per reviewer.
- Substituting --chain for actual triage — chain produces more findings, not pre-triaged findings
- Running --chain on Suggestion-only first-pass results — wasted async round-trip
- Letting Reviewer 2 see Reviewer 1's evidence trail in the packet — contaminates independent re-read; addendum should expose findings only, not evidence URIs (or expose with explicit instruction to re-derive)
- Treating the chain as 'gpt-5.5 then claude-web' specifically — the pattern generalizes to any (Reviewer1, Reviewer2) pair; for **panel** work prefer skeptic→Grok then reviewer→GPT (optional synthesizer→Gemini), matching `panel_dispatch`
- Omitting **decisive_falsifier** on skeptic or lead adjudication output — session-close `panel_disposition_incomplete` will flag incomplete panel stamps

## Empirical basis

- Thread 1086 (multi-model review of master @ f7d92fc7): demonstrated the workflow end-to-end
- RAG: bitsai-cr-automated-review.pdf — multi-model coverage reduces false negatives
- RAG: systematic-failures-code-verification.pdf — single-model review systematically misses the same classes of issues across passes; adversarial pairs improve recall

## Related

- /session-review, /diff-review (current commands; consume this skill)
- todo:review-chain-mode-design (future --chain flag spec; depends on this skill)
- agent_skill:dispatch-workflow (canonical dispatch patterns)
- agent_skill:mode-b-web-orchestrator (when web-claude is Reviewer 2 dispatching grokbuild as Reviewer 1)
- agent_skill:implementation-plan-workflow (when the review is one phase of a multi-phase plan deck)
- .cursor/skills/review-task-guidance/SKILL.md (sister skill in plan:review-workflow-improvement)
