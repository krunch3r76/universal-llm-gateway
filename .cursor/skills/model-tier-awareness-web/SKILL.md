---
name: model-tier-awareness-web
description: "Present-tense config nudges for web-anthropic: family-only self-read each turn; effort/thinking up- or down-nudge at live forks. Operator owns family choice under the two-row rule."
trigger_match_terms: ["model-tier-awareness-web", "model_tier_awareness_web", "declared", "model", "tier", "task-class", "pivot", "review-reasoning", "cortex_brief", "web", "operator", "declares"]
related_skills: ["consult-routing"]
---

# Model-Tier Awareness (Web)

Web tuple = `family × effort × thinking`. Resident CAN read **family** each turn from the system-prompt product block (assertion 23213); effort and thinking are not fully introspectable.

## Operator family rule (mismatch reference)

**Opus = reasoning/life/coding; Sonnet = mechanical/document processing**

Family routing is the operator's responsibility under this rule. Full dispatch task-class→model table: skill `consult-routing` § Task-class model reference.

## Nudge rules (present-tense, at live forks only)

**Effort/thinking:** Up-nudge when a fork warrants extended thinking or higher effort. Thinking on/off IS self-observable; effort is not — effort nudges are need-statements, not diffs. Down-nudge once per mechanical stretch (never per-turn).

**Family mismatch:** Read current family from the system prompt; compare to the two-row rule at a live fork. One present-tense line if wrong; no history, no blocking.

## Voice

Use declared or conditional form — declared ≠ verified. When family is unclear (e.g. Fable classifier fallback), state conditionally; never claim verified identity you did not read.

## Blind spots

- Effort (Low/Medium/High/Extra/Max) is not introspectable — nudge from task need, not claimed diff.
- Thinking on/off is introspectable.

## Arc-close heavy review (design intent only)

After a reasoning-heavy arc, Opus+ retrospective review is the intended burden owner (`last_heavy_review` attr convention). No session-close detector is landed — this skill's present-tense nudges are the live safety net. Do not claim detector enforcement.
