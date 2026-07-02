---
name: agent-guidance-writing
description: Use when authoring or reviewing agent guidance in this workspace: `.cursor/rules/*.mdc`, `.cursor/skills/*/SKILL.md`, or `docs/agent-guides/` canonical docs.
---

# Agent Guidance Writing

`agent_guidance ⇒ durable instruction for agents, not human-facing documentation`.

## Forms

| Form | Location | Load trigger | Use |
|---|---|---|---|
| Always rule | `.cursor/rules/*.mdc` (`alwaysApply: true`) | Every turn | Never-violate invariants |
| Description-gated rule | `.cursor/rules/*.mdc` (`alwaysApply: false`) | Description match | Contextual discipline |
| Skill | `.cursor/skills/*/SKILL.md` | Explicit read | Multi-step playbooks |
| Canonical doc | `docs/agent-guides/skills/*.md` | `fs(workspaces, read, ...)` | Authoritative SOT; Cursor forms defer here |

`alwaysApply ∧ canonical_doc_exists ⇒ inline_only(required_invariants) ∧ defer_bulk_to_canonical`.

## Required shape

Frontmatter on all `.mdc` / `SKILL.md`:
- `name`: slug matching filename.
- `description`: precise trigger terms.
- `applicable_agents` optional on `SKILL.md`; JSON list (`["claude-cursor"]`, `["claude-web","claude-cursor"]`, `["*"]`). Default `["*"]`. `ingest_skills.py` reads it; omitted PATCH preserves live partition.

Body:
- First line actionable; no motivational opener or name restatement.
- Tables for decision matrices ≥3 rows.
- Invariants as single-line `∀` / `¬` clauses.
- No "This skill helps you..." or overview paragraph before instructions.

## Line-count targets

| Guidance type | Target |
|---|---|
| Thin stub | ≤15 lines |
| Simple routing/invariant | ≤30 lines |
| Medium procedural skill/rule | ≤80 lines |
| Complex multi-step/reference tables | ≤150 lines |
| >200 lines | Split or defer to supporting file |

## Token economy

- Always rules: inline only invariant lines; defer bulk with `fs(workspaces, read, ...)`.
- Skills: use trigger line + SOT deferral, OR inline only if ≤40 lines total.
- Large reference content ⇒ canonical doc + supporting `.md` files.

## Thin-stub pattern

When `docs/agent-guides/skills/<slug>.md` exists, Cursor trigger content should be ≤10 lines:

```markdown
---
name: <slug>
description: <trigger terms>
---
fs(sandbox="workspaces", op="read", path="universal-llm-gateway/docs/agent-guides/skills/<slug>.md")
```

`cursor_stub.lines > 15 ⇒ convert_to_thin_stub`; never duplicate canonical content.

## Correct vs anti-pattern

| Concern | Correct | Anti-pattern |
|---|---|---|
| Opener | Frontmatter → actionable table/rule | Paragraph explaining purpose |
| Lists | Table per concern | Bullet lists ≥3 items where table fits |
| Cursor stub | SOT pointer + `fs(...)` | Full playbook inline |
| Registration | Cortex `agent_skill` entity verified | Filesystem-only `SKILL.md` |

## Conformance checklist

1. Trigger exists (`description:` or explicit "Use when").
2. Body starts actionable.
3. Line count fits tier.
4. Tables replace prose where ≥3 comparable items.
5. No duplication of alwaysApply content.
6. Registered: run `scripts/cortex/ingest_skills.py`; verify `cortex(entity_get agent_skill:<slug>)` resolves with correct `source_uri` and appears in `GET /boot-skills`. Filesystem-only skills are invisible to `skill_suggest`, boot-skills, web, and dispatch seats.

## Universal procedure only (binding)

Skills and rules carry **universal procedure** — not personal or matter-specific facts.

**Split test:** *Would this sentence still be true for a different operator's unrelated matter?* If **no**, it does not belong in a skill or rule. Put it on a case-scoped `document:` entity (matter playbook), not in the guidance index.

| Belongs in skill/rule | Does NOT belong |
|---|---|
| Tool dispatch shapes, session protocols, verification gates (generic) | Contacts, dollar amounts, case IDs, property addresses |
| Posture and method usable on any task | Steps that only make sense for one active case |
| Invariants about how agents work here | Operator-specific financial or legal state |

Matter playbooks → `document:` on `case:` entities, discovered via `has_playbook` edges — not `agent_skill` rows with exclusion attributes. Full policy: `fs(sandbox="cortex", op="read", path="notes/system/specs/skill-guidance-policy.md")`.
