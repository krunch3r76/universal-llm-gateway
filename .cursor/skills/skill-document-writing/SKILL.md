---
name: skill-document-writing
description: "When authoring, revising, compressing, retiring, or auditing a SKILL.md / agent_skill — orient, then drive from the Cortex workflow map."
trigger_match_terms: ["author skill", "write skill", "new skill", "revise skill", "compress skill", "retire skill", "audit skill", "critique skill", "skill-authoring", "skill-document-writing", "SKILL.md", "ingest_skills", "register skill"]
related_skills: ["frontier-model-instructions", "agent-guidance-writing", "corpus-map-authoring", "corpus-grounded-skill-authoring", "cursor-rule-authoring"]
---

# Skill Document Writing

`skill_touch ⇒ orient(here) → drive(workflow map) → register`.

## SOT chain

catalog `config/skills.yaml` → SOT `.cursor/skills/{slug}/SKILL.md` (`.claude/skills` if `life_local`; plugin census wins when the file exists) → entity via `ingest_skills.py` → generated `.claude/skills` (never hand-edit `shared_sync`).
Sole placement: catalog row (`surface_class` + `mcp_surface_required`). ¬ invent list membership.

## Scenario → load

| Scenario | Load, in order |
|---|---|
| Trivial edit | this |
| New procedural | `frontier-model-instructions` → this → `agent-guidance-writing` |
| Domain / knowledge | `corpus-map-authoring` → `corpus-grounded-skill-authoring` → `frontier-model-instructions` → this |
| Compression / substantive | `frontier-model-instructions` → this → digest + workflow map |
| New Cursor bootstrap | `create-skill` + procedural row |
| `.mdc` rule (not a skill) | `frontier-model-instructions` → `cursor-rule-authoring` |

`substantive ⇔ new_section ∨ taxonomy_reclassify ∨ trigger_rewrite ∨ ≥1_rule_changed`.

## Drive surfaces (read live)

| URI | Role |
|---|---|
| `cortex://notes/system/references/skillreducer-research-excerpts.md` | digest — taxonomy + gates |
| `cortex://notes/system/references/skill-compression-workflow-map.md` | drive — Phase A–D, sidecar, Phase D |
| `cortex://notes/system/specs/skill-guidance-policy.md` | universal-procedure split test |
| `decision:claude-ai-skill-description-limits-by-surface` | description ceiling |
| `agent-guidance-writing` | line budgets, table shape, registration |

## Gates that stay here

`substantive_pass ∨ compression_pass ⇒ fs_read(digest ∧ workflow map) before classify`. `packet_corpus_present ⇏ fixed_overhead_satisfied` (thread 4034).
¬ hand-edit generated `.claude/skills/` when `surface_class=shared_sync`.
`edit(SKILL.md) alone ⇒ invisible` — `ingest_skills.py` then `--check`; verify `entity_get` + `GET /boot-skills`.
∀ touch: adjudicate open `CANDIDATE SKILL REVISION` (`review_status=committed` or `rejected`); ¬ leave active. Protocol: workflow map.
Frontmatter `description:` ≤200 chars; no angle-bracket XML tags.
Discoverable set = `lifecycle=active`.
`section ∈ L2(this) ∧ section ∈ fixed_overhead ⇒ relocate into the drive first`.

## Lifecycle

`author ⇔ recurring ∧ past_judgment_failed`. Retire = `lifecycle=retired` + `supersedes` edge; `[universal:no-bc]` deletes the SOT directory + catalog row and keeps the entity.
`retire(agent_skill) ∧ surface_class ∈ {shared_sync, life_local} ⇒ same_turn(claude-ai-skill-uninstall) ∧ verify(slug ∉ extra_on_ui)`. Deleting the local SOT/catalog row is one-way — it does not retract an already-uploaded Customize copy. `cursor_only` skips (never uploaded). Ground: `sms-bridge-mailbox`/`sms-tool-dispatch` survived on Customize for 2 days past local retirement because no AC named the UI surface (`a:31335` → `a:31820`).
`self_rewrite ⇒ identical_loop` (backup → classify → rewrite SOT → sidecar → Phase D).
