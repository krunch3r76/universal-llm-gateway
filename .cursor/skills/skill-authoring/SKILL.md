---
name: skill-authoring
description: "When writing, revising, compressing, or registering a skill — load-order router, registration pipeline, and companion-skill map. Defer mechanics to skill-document-writing."
trigger_match_terms: ["skill-authoring", "skill_authoring", "write skill", "author skill", "new skill", "revise skill", "compress skill", "skill writing", "register skill", "ingest_skills", "SKILL.md"]
related_skills: ["skill-document-writing", "agent-guidance-writing", "frontier-model-instructions", "corpus-grounded-skill-authoring", "corpus-map-authoring", "cursor-rule-authoring"]
---

# Skill Authoring

`skill_work ⇒ load(this_skill) ∧ resolve_companion_stack ∧ defer_depth_to(skill-document-writing)`.

Routing skill only — SOT for form, taxonomy, compression, and lifecycle is `skill-document-writing`.

## Invariant

`author|revise|compress|retire(SKILL.md) ⇒ catalog_row ∧ SOT per surface_class`.
Sole placement authority: `config/skills.yaml` (`surface_class` + `mcp_surface_required`).
SOT paths:
`shared_sync` / `cursor_only` → `workspaces://universal-llm-gateway/.cursor/skills/{slug}/SKILL.md`;
`life_local` → `workspaces://universal-llm-gateway/.claude/skills/{slug}/SKILL.md`.
¬ hand-edit a shared-sync `.claude/skills/` render or legacy `agent-skills/` mirrors.
¬ invent list membership (`UI_TARGET_*`, GAP, SHARED_SYNC constants) — edit the catalog row.

## Load order by scenario

| Scenario | Load, in order |
|---|---|
| New procedural skill | `frontier-model-instructions` → `skill-document-writing` → `agent-guidance-writing` |
| Domain / knowledge skill | `corpus-map-authoring` → `corpus-grounded-skill-authoring` → `frontier-model-instructions` → `skill-document-writing` |
| Trivial edit (typo, single rule, metadata) | `skill-document-writing` only |
| Compression / substantive revision | `frontier-model-instructions` → `skill-document-writing` + fixed overhead (below) |
| New Cursor skill bootstrap | `create-skill` + procedural row above |
| `.mdc` rule (not a skill body) | `frontier-model-instructions` → `cursor-rule-authoring` |

`substantive ⇔ new_section ∨ taxonomy_reclassify ∨ trigger_rewrite ∨ ≥1_rule_changed`.

## Fixed overhead (substantive ∨ compression)

`substantive_pass ∨ compression_pass ⇒ fs_read(both) before classify`:

| URI | Role |
|---|---|
| `cortex://notes/system/references/skillreducer-research-excerpts.md` | SkillReducer taxonomy + gates |
| `cortex://notes/system/references/skill-compression-workflow-map.md` | Per-slug steps, sidecar contract, Phase D |

Record read receipt (`path | sha256`) in compression sidecar before editing. `packet_corpus_present ⇏ fixed_overhead_satisfied`.

## Companion map

| Skill | Role |
|---|---|
| `skill-document-writing` | Form, L1/L2/L3, taxonomy, compression, lifecycle, CANDIDATE REVISION adjudication |
| `agent-guidance-writing` | Line budgets, table shape, thin stubs, universal-procedure split test |
| `frontier-model-instructions` | FOL voice for skill bodies — ¬ `prose-discipline` |
| `corpus-map-authoring` | Durable corpus map before domain encoding |
| `corpus-grounded-skill-authoring` | Evidence → claims; `¬corpus ⇒ ingest first` |
| `create-skill` | Cursor directory layout; personal vs project placement |
| `cursor-rule-authoring` | `.mdc` file mechanics — rules ¬ skills |

Corpus prep: `research-article-search` → `document-ingestion`.

## Registration (¬ optional)

`edit(SKILL.md) alone ⇒ invisible`. For a **new** slug: add the catalog row in
`config/skills.yaml` first (`surface_class`, `mcp_surface_required`; Anthropic Customize
admission requires `surface_class ∈ {shared_sync, life_local}` ∧ `mcp ≠ code`).
Then after every SOT write:

```bash
python scripts/cortex/ingest_skills.py
python scripts/cortex/ingest_skills.py --check
```

Verify: `cortex(entity_get, agent_skill:{slug})` + `GET /boot-skills`. Anthropic Customize
targets only: `gen_claude_bundles.py --check` then `/claude-ai-sync` (cursor_only
never uploads).

Glob rule `.cursor/rules/skill-authoring_ws.mdc` fires on `.cursor/skills/**/SKILL.md` edits.

## Universal-procedure gate

*Would this sentence still be true for a different operator's unrelated matter?* `no ⇒ document:` on case entity, ¬ `agent_skill`. Policy: `cortex://notes/system/specs/skill-guidance-policy.md`.

## Domain skill pipeline

```
¬indexed_corpus ⇒ research-article-search → document-ingestion
¬map_digest ⇒ corpus-map-authoring (Phases 0–5)
⇒ corpus-grounded-skill-authoring (grounding sidecar + entity assertions)
⇒ skill-document-writing stack
⇒ ingest_skills.py
```

`∀ load_bearing_rule : ∃ source` (chunk_id, doc URI, or entity assertion).

## Compression quick path

1. Fixed overhead read + sha256 receipt
2. `entity_get` → `source_uri` → `fs read` (+ backup)
3. Classify → rewrite SOT only
4. Sidecar: `cortex://notes/system/threads/compression-results/{slug}.md`
5. Gate 1: operational concepts preserved in L2 ∪ L3
6. `ingest_skills.py` + `--check`

Compression-floor slugs (operator go required): `operator-posture`, `completion-provenance-discipline`, `cortex-provenance-discipline`, `git-posture`, `evidence-review-discipline`, session-close provenance slugs.

## Anti-patterns

| ✗ | ✓ |
|---|---|
| Filesystem-only SKILL.md | `ingest_skills.py` + entity verify |
| Domain claims from priors | Corpus-grounded stack |
| `prose-discipline` on skill bodies | `frontier-model-instructions` |
| Skip fixed overhead on substantive pass | Read digest + workflow map |
| Edit generated `.claude/skills/` | Edit `.cursor/skills/` SOT |
| Open `CANDIDATE SKILL REVISION` ignored | Adjudicate before close — `skill-document-writing` § CANDIDATE SKILL REVISION |

## Line budgets (summary)

| Type | Target lines |
|---|---|
| Thin stub | ≤15 |
| Simple routing | ≤30 |
| Medium procedural | ≤80 |
| Complex + tables | ≤150 |

Full tiers: `agent-guidance-writing`.

## Related skills

- skill-document-writing
- agent-guidance-writing
- frontier-model-instructions
- corpus-grounded-skill-authoring
- corpus-map-authoring
