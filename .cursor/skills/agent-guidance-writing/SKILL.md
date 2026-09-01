---
name: agent-guidance-writing
description: "Use when authoring or reviewing agent guidance in this workspace: `.cursor/rules/*.mdc`, `.cursor/skills/*/SKILL.md`, or `docs/agent-guides/` canonical docs."
---

# Agent Guidance Writing

`agent_guidance ⇒ durable instruction for agents, not human-facing documentation`.

## Forms

| Form | Location | Load trigger | Use |
|---|---|---|---|
| Always rule | `.cursor/rules/*.mdc` (`alwaysApply: true`) | Every turn | Never-violate invariants |
| Description-gated rule | `.cursor/rules/*.mdc` (`alwaysApply: false`) | Description match | Contextual discipline |
| Skill | `.cursor/skills/*/SKILL.md` | Explicit read | Multi-step playbooks |
| Skill body | `.cursor/skills/<slug>/SKILL.md` | Entity `source_uri` / explicit read | Authoritative playbook; cortex-mount SOT when `sot: cortex` |

`alwaysApply ∧ canonical_doc_exists ⇒ inline_only(required_invariants) ∧ defer_bulk_to_canonical`.

## Required shape

Frontmatter on all `.mdc` / `SKILL.md`:
- `name`: slug matching filename.
- `description`: precise trigger terms.
- Placement is **not** frontmatter: add/update the slug row in `config/skills.yaml` (`surface_class` + `mcp_surface_required`). Runtime discovery filters on `capabilities_required` (catalog-derived), not seat-name lists. `applicable_agents` on SKILL.md is retired placement metadata — do not reintroduce as a filter.

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

## Deferral: mirror, split, or inline — never redirect

`pointer_followed ⟸ mechanism, ¬ motivation`. A redirect resolves only when
something other than the reader's judgment resolves it.

| Resolver | Reliable | Examples |
|---|---|---|
| Substrate injects the body | yes | Cursor `<available_skills>` → Read; GIW `resolve_prompt_preamble`; Stargate `<invariants>` enrich; `ensure_cdp_judgment_skills` |
| Generated mirror | yes | plugin census `cp -a`; `shared_sync` `render_bundle` (which strips pointers on purpose) |
| Prose pointer in a body | **no** | thin stub; "full body at X"; "detail in `runbook:foo`" |

**Skip-safety gate:** `defer(content) ⇒ skip(fetch) ⇏ wrong_action`. Deferred
content must be explanatory or rare-path. If skipping the fetch changes what the
agent *does* on the common path, it stays resident. A well-written stub reads as
sufficient and so suppresses its own follow-through — compression quality and hop
compliance move in opposite directions.

Order of preference when a body is too large:
1. **Split by trigger** — N narrower slugs with precise descriptions; harness
   matching selects, no hop.
2. **Compress in place** — delete, don't defer
   (`preserve(normative_content) ∧ ¬add_content`).
3. **Mirror** — cross-surface delivery is a generated copy, never a cross-tree
   redirect. `¬ redirect(.cursor ↔ .claude)`.
4. **Defer** — only skip-safe content, and gate the pointer on a required
   *output* ("cannot report a claim class without X"), never on context.

Resident `skill-surface_ulg` forbids fs-reading skill SOT into context. A stub
whose body is an `fs(read SOT)` instruction contradicts it and will not be
followed. `cursor_stub.lines > 15 ⇒ convert_to_thin_stub` is retired.

## Correct vs anti-pattern

| Concern | Correct | Anti-pattern |
|---|---|---|
| Opener | Frontmatter → actionable table/rule | Paragraph explaining purpose |
| Lists | Table per concern | Bullet lists ≥3 items where table fits |
| Cursor stub | SOT pointer + `fs(...)` | Full playbook inline |
| Registration | Cortex `agent_skill` entity verified | Filesystem-only `SKILL.md` |
| Parked revisions | Adjudicate `CANDIDATE SKILL REVISION` on touch | Edit guidance while audit candidates stay open |

## Conformance checklist

1. Trigger exists (`description:` or explicit "Use when").
2. Body starts actionable.
3. Line count fits tier.
4. Tables replace prose where ≥3 comparable items.
5. No duplication of alwaysApply content.
6. Registered: row in `config/skills.yaml` + run `scripts/cortex/ingest_skills.py`; verify `cortex(entity_get agent_skill:<slug>)` resolves with correct `source_uri` / projected `surface_class` and appears in `GET /boot-skills`. Filesystem-only skills (no catalog row / no ingest) are invisible to boot-skills, web, and dispatch seats.
7. **Open `CANDIDATE SKILL REVISION` assertions adjudicated** on this entity before closing the touch — audit-visible via `agent_skill_revision_candidate_unadjudicated`; accept (`committed` / applied supersede) or reject (`review_status=rejected`). Full protocol: `cortex://notes/system/references/skill-compression-workflow-map.md` § CANDIDATE SKILL REVISION.

## Universal procedure only (binding)

Skills and rules carry **universal procedure** — not personal or matter-specific facts.

**Split test:** *Would this sentence still be true for a different operator's unrelated matter?* If **no**, it does not belong in a skill or rule. Put it on a case-scoped `document:` entity (matter playbook), not in the guidance index.

| Belongs in skill/rule | Does NOT belong |
|---|---|
| Tool dispatch shapes, session protocols, verification gates (generic) | Contacts, dollar amounts, case IDs, property addresses |
| Posture and method usable on any task | Steps that only make sense for one active case |
| Invariants about how agents work here | Operator-specific financial or legal state |

Matter playbooks → `document:` on `case:` entities, discovered via `has_playbook` edges — not `agent_skill` rows with exclusion attributes. Full policy: `fs(sandbox="cortex", op="read", path="notes/system/specs/skill-guidance-policy.md")`.
