---
name: claude-ai-skill-uninstall
description: "On removing a skill from claude.ai Customize Skills (retired, demoted, or cut from the life set): status scan, per-slug uninstall via Jupiter, re-scan verify. Uninstall is not upload-replace."
trigger_match_terms: ["claude-ai-skill-uninstall", "uninstall skill", "remove skill from claude.ai", "extra_on_ui", "skill uninstall"]
related_skills: ["claude-ai-bundle-sync"]
---

# Skill: claude-ai-skill-uninstall

1. Preconditions — slug already removed or demoted out of catalog Claude.ai targets (`config/skills.yaml` → not in `claude_ai_targets()`) and regen'd; otherwise `status` will not flag it and the next `upload --all` resurrects it.
2. Step 1 status — `claude-ai-sync-jupiter status`; confirm slug in `extra_on_ui` (or listed on UI).
3. Step 2 uninstall — `claude-ai-sync-jupiter uninstall --slugs {a,b} [--continue-on-error]`; UI path: row → detail → "More options for {slug}" → **Uninstall** → confirm.
4. Step 3 verify — re-run `status`; expect parity in sync, slug absent from `extra_on_ui`; closeout cites before/after status lines.
5. Seat split — shell seats run the Jupiter wrapper; claude.ai seats hand off via bus with the slug list; never mass-uninstall outside an operator-gated reconcile decision.
