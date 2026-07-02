# Unified skills (procedural playbooks)

**Readers:** web-claude, connector, Cursor (via thin `.cursor/skills` stubs).

## Surface model

| Surface | Membership | Role |
|---|---|---|
| **SOT** | one body per slug | `docs/agent-guides/skills/` (git), cortex `agent-skills/`, or `.cursor/skills/` (IDE-authored) |
| **`.cursor/skills/<slug>/`** | `CURSOR_INDEXED_SLUGS` (91) | Hardlink → SOT (gitignored) |
| **`.claude/skills/<slug>/`** | `CLAUDE_BUNDLE_SLUGS` (= indexed minus matter retiring) | Rendered self-contained bundle (gitignored) |
| **`agent_skill:*`** | boot index | `source_uri` → SOT; `skill_suggest` / boot manifest |

**Invariant:** every `CURSOR_INDEXED` skill renders to `.claude/skills/` and hardlinks to `.cursor/skills/`. Matter playbooks (waves B–C) retired 2026-07-02 → `document:` + `has_playbook`.

**Retired (eliminated 2026-07-02):** `delegate-to-grok` (deprecated dir removed), `superheavy-dispatch` (retired entity + SOT removed; use `web-consult` / `panel_dispatch`).

Regenerate after SOT edits:

```bash
python3 scripts/cortex/gen_claude_bundles.py
python3 scripts/cortex/gen_claude_bundles.py --check
```

Lists: `libs/claude_bundles/resolver.py`.

## Orphan audit (2026-07-02)

| Check | Result |
|---|---|
| `CURSOR_INDEXED` missing `.cursor/skills/` | **0** (91/91) |
| `.cursor/skills/` not in `CURSOR_INDEXED` | **0** |
| `CLAUDE_BUNDLE` missing `.claude/skills/` | **0** (90 bundled + 1 matter-only hei) |
| Added to index | `add-mcp-tool`, `produce-uml` |
| Removed | `delegate-to-grok`, `superheavy-dispatch` |

Do not hand-maintain duplicate long-form copies in cortex or `.cursor/skills`.
