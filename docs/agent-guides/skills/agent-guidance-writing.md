---
name: agent-guidance-writing
description: Use when authoring or reviewing any agent guidance document in this workspace (.cursor/rules/*.mdc, .cursor/skills/*/SKILL.md, or docs/agent-guides/).
---

# Agent Guidance Writing

## Vocabulary

Three forms, one purpose — give agents durable instructions:

| Form | Location | Load trigger | Use for |
|---|---|---|---|
| Always rule | `.cursor/rules/*.mdc` (`alwaysApply: true`) | Injected every turn | Invariants that must never be violated |
| Description-gated rule | `.cursor/rules/*.mdc` (`alwaysApply: false`) | Injected when description matches | Contextual discipline for specific task types |
| Skill | `.cursor/skills/*/SKILL.md` | Agent reads explicitly | Multi-step procedural playbooks |
| Canonical doc | `docs/agent-guides/skills/*.md` | `fs(workspaces, ...)` by any agent | Authoritative content; Cursor forms defer here |

∀ alwaysApply rule linking to a canonical doc: inline ONLY the invariant lines required every turn; defer bulk content to the canonical doc.

## Structure (all forms)

**Frontmatter** (required on all `.mdc` / `SKILL.md` files):
- `name`: slug matching the filename
- `description`: trigger terms — the agent uses this to decide whether to load

**Body discipline**:
- First line of body: actionable content — no motivational opener, no name restatement
- Tables > prose for decision matrices ≥3 rows
- Invariants as `∀`/`¬` single-line clauses
- ¬ "This skill helps you…" openers
- ¬ overview paragraphs before actionable content

## Line-Count Targets

| Guidance type | Target |
|---|---|
| Thin stub (Cursor trigger, defers to canonical) | ≤15 lines |
| Simple routing/invariant rule | ≤30 lines |
| Medium procedural skill/rule | ≤80 lines |
| Complex multi-step with reference tables | ≤150 lines |
| Over 200 | Always split or defer to supporting file |

## Token Economy

- alwaysApply rules: inline only the invariant lines; defer bulk via `fs(workspaces, ...)`
- Skills: trigger line + `fs(...)` deferral, OR inline if ≤40 lines total
- Progressive disclosure: canonical doc → supporting `.md` files for large reference corpora

## Thin-Stub Pattern

When a canonical doc exists in `docs/agent-guides/skills/`, the Cursor trigger should be:

```
---
name: <slug>
description: <trigger terms>
---
fs(sandbox="workspaces", op="read", path="universal-llm-gateway/docs/agent-guides/skills/<slug>.md")
```

≤10 lines. ¬ duplicate canonical content.

## Compact vs Verbose

| | Compact (correct) | Verbose (anti-pattern) |
|---|---|---|
| Opener | Frontmatter → table immediately | Paragraph explaining what the skill does |
| Lists | Table per concern | Bullet lists ≥3 items |
| Cursor stub | SOT pointer + `fs(...)` deferral, ≤15 lines | Full playbook inline in `.cursor/skills/` |
| Exemplar | `dispatch-shape/SKILL.md` (35 lines): SOT pointer + quick-rule table + escape hatch | `service-lifecycle/SKILL.md` (84 lines inline in `.cursor/skills/`): full routing tables baked into the stub instead of deferred to a canonical doc |

**Key signal**: if a `.cursor/skills/` file exceeds 15 lines, it should defer its bulk to `docs/agent-guides/skills/<slug>.md` and become a thin stub.

## Conformance Checklist

For any guidance doc before shipping:

1. Has a trigger line (`description:` frontmatter or explicit "Use when" sentence)?
2. Body starts with actionable content (not motivational prose)?
3. Within line-count target for its complexity tier?
4. Tables used where prose would exceed 3 lines?
5. No duplication of content already injected by an alwaysApply rule?
