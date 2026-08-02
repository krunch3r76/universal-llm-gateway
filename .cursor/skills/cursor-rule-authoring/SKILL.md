---
name: cursor-rule-authoring
description: "When authoring, compressing, placing, or splitting .mdc Cursor rules — alwaysApply vs agent-requestable, frontmatter shape, and placement discipline."
trigger_match_terms: ["cursor-rule-authoring", "cursor_rule_authoring", "author", "compress", ".mdc", "cursor", "rule", "writing-documents", "place", "split", "deciding", "alwaysapply"]
---

# Cursor Rule Authoring

**Version:** 1.0
**Created:** 2026-06-05
**Authority level:** HIGH. `.mdc` rules in `.cursor/rules/` are the always-applied procedural surface every Cursor seat loads each turn.

## When to read this skill

- Authoring a new `.mdc` rule in `.cursor/rules/` (projects root or a repo's `.cursor/rules/`).
- Compressing or rewriting an existing rule for boot footprint.
- Deciding whether a rule belongs in always-applied context or as an agent-requestable rule.
- Splitting an over-broad rule, or triaging which rules carry the per-turn budget.

If composing the rule's prose (FOL, voice, examples, the compression floor for guardrails), that is owned by `frontier-model-instructions` — load it first. This skill owns the `.mdc` FILE: frontmatter, placement, budget, structure.

## Companion skills

`authoring_or_compressing_mdc_rule ⇒ load(frontier-model-instructions) ∧ load(this_skill)`

- `frontier-model-instructions` — prose discipline for all model-targeted text, INCLUDING the compression floor (prior-override / guardrail rules), calibrate-to-weakest-reader, and compression mode. This skill does not duplicate it.
- `skill-document-writing` — the SKILL.md analog (L1/L2/L3, registration ritual). Rules are not skills: no entity, no README row, no partition script. A rule loads from disk by `alwaysApply` or by description match.
- `markdown-navigation` — rules are read via `md_read(section=...)`; the section skeleton below is what makes that work.

## Core rule

A `.mdc` rule earns `alwaysApply: true` iff its guidance is needed on most turns AND cannot be reliably triggered by a description. Otherwise it is agent-requestable (`alwaysApply: false`) with a strong trigger description. Always-applied bytes are paid every turn × every session × every seat; budget them accordingly.

`needed_most_turns ∧ ¬reliably_trigger_describable ⇒ alwaysApply:true`
`situational ∨ strong_trigger_describable ⇒ alwaysApply:false ∧ description = imperative_trigger`

## Frontmatter contract

```
---
description: <imperative trigger sentence — per frontier-model-instructions; the dispatch mechanism for alwaysApply:false rules>
globs: <file-pattern — rule applies when matching files are open; omit for non-file-scoped rules>
alwaysApply: <true | false>
---
```

- `alwaysApply:false ⇒ description carries the full firing load` — it is the only thing the agent sees until the rule loads. Make it a precise imperative trigger; topic-only descriptions never fire.
- `alwaysApply:true ⇒ description still required` for rule-picker display, but the body loads regardless.

## alwaysApply triage

Demote to `alwaysApply:false` when either holds:
- The boot manifest or another always-on surface already carries the signal (the rule only re-states what boot surfaces).
- The need is situational and the trigger is describable in one imperative sentence (e.g. "load before any fs read of a large .md/.mdc file").

`boot_surfaces_signal ∨ (situational ∧ trigger_describable) ⇒ demote`

Keep `alwaysApply:true` for cross-cutting invariants that fire unpredictably and silently (provenance, scope, identity, git-state safety) — a missed trigger there is a guardrail breach, not a missed convenience.

## Budget by applicability

`cost(alwaysApply:true) = bytes × turns × sessions × seats`; `cost(alwaysApply:false) = bytes × fire_count`.

- `alwaysApply:true`: tightest budget. Invariant + enumerations + the irreducible examples only. Push everything else to a demoted rule or out.
- `alwaysApply:false`: looser body budget; pays only when triggered. Detail that would bloat an always-on rule can live here.

Compressing an always-applied rule is the highest-leverage byte reduction in the system. Subject it to `frontier-model-instructions` token economy AND its compression floor — do not strip prior-override enumerations to hit a number.

## Section skeleton (navigability)

Rules are read partially via `md_read(section=...)`. A stable, predictable heading skeleton is behavior-relevant: a heading-less blob forces full-file loads and defeats the budget it was meant to serve.

Canonical skeleton (skip what does not apply; do not invent low-information headers):
```
# <Rule Title>
**Invariant**: <one boxed sentence>
## Forbidden / ## Permitted   (guardrail rules)
## <procedural sections>
## Anti-patterns   (bad | good, targeting the prevented failure)
```

`∀ rule : section_headings stable ∧ information_carrying ⇒ md_read(section) resolves`

## Anti-patterns

| Bad | Good |
|---|---|
| New rule defaults to `alwaysApply:true` | Triage; default situational rules to `false` + strong trigger |
| `alwaysApply:false` rule with a topic-only description | Imperative trigger sentence; it is the entire dispatch |
| Always-applied rule re-stating what boot already surfaces | Demote; reference the boot signal |
| Collapse a rule into one heading-less blob to save bytes | Keep the section skeleton; cut within sections |
| Strip a guardrail's enumeration/examples to hit a token target | Apply the compression floor (frontier-model-instructions) |

## Examples

- **Demotion**: `md-navigation_ws` and `agent-skills_ws` carried always-on bytes but were situational and describable; flipped to `alwaysApply:false` with strong triggers (the boot manifest already surfaces skills). Net: off the per-turn budget entirely.
- **Floor held**: `git-revert-scope` and `provenance-discipline` stayed `alwaysApply:true` and kept their forbidden/permitted lists and bad/good pairs even under aggressive compression — prior-override rules; a missed firing is a breach, not a lost convenience.

## Minimal operating summary

- `alwaysApply:true` iff needed-most-turns AND not trigger-describable; else `false` + imperative trigger description.
- Always-on bytes cost turns × sessions × seats — budget them tightest.
- Keep a stable, information-carrying section skeleton so `md_read(section)` resolves.
- Prose, compression discipline, and the guardrail compression floor live in `frontier-model-instructions` — load it first; this skill is the `.mdc` file mechanics only.
