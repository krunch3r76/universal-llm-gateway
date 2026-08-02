# Cursor skills — canonical skill-body store

Skill bodies live **only** here (`.cursor/skills/<slug>/SKILL.md`) and as script-managed
hardlinks under `.claude/skills/<slug>/SKILL.md`. There is no parallel legacy docs-side duplicate tree.

- **Authoritative path:** `.cursor/skills/<slug>/SKILL.md`
- **Table resolution:** `libs/implement_admission/skill_source_table.py` (generated from Cortex entity `source_uri`)
- **Regenerate claude.ai bundle hardlinks:** `python3 scripts/cortex/gen_claude_bundles.py`

Companion/deferred-reference files live alongside `SKILL.md` under the slug directory.
