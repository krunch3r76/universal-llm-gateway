# Claude.ai bundle sync workflow

Operational runbook for keeping **claude.ai Customize → Skills** aligned with
local `CLAUDE_BUNDLE_SLUGS` (currently 90 skills; matter playbooks retired to case documents).

**Cursor command:** `/claude-ai-sync` (`.cursor/commands/claude-ai-sync.md`)  
**Cortex checkpoint:** `decision:claude-ai-skill-upload-automation` · agent-bus thread 4050.

## What this is / is not

| Store | Mechanism | Populated by |
|---|---|---|
| **claude.ai Customize → Skills** | UI zip/md upload | `upload_claude_bundles_ui.py` (Playwright + CDP) |
| **Anthropic Skills API** | `POST /v1/skills` | `upload_claude_bundles.py --api` — **wrong store** for UI parity |

## Prerequisites (Jupiter or local)

1. Chrome with remote debugging (logged into claude.ai):

   ```bash
   python scripts/cortex/upload_claude_bundles_ui.py --print-chrome-cmd
   # run the printed google-chrome … command, then open Customize → Skills
   ```

2. Python venv: `$HOME/.venvs/universal`

## Routine — regen → scan → upload

```bash
cd /mnt/torus/projects/universal-llm-gateway

# 1. Render .claude/skills from SOT + validate descriptions
python scripts/cortex/gen_claude_bundles.py
python scripts/cortex/gen_claude_bundles.py --check

# 2. Read-only drift scan (local index vs live Skills table)
python scripts/cortex/upload_claude_bundles_ui.py --status

# 3. Upload NEW slugs only (default — skips table hits)
python scripts/cortex/upload_claude_bundles_ui.py --all --continue-on-error

# 4. After bundle content changes — refresh skills already on UI
python scripts/cortex/upload_claude_bundles_ui.py --all --replace --continue-on-error
```

Exit code from `--status`: **0** = parity OK, **1** = missing or invalid local bundles.

## Change detection layers

| Layer | Command | Detects |
|---|---|---|
| SOT → bundle render | `gen_claude_bundles.py --check` | Missing SOT, bad descriptions, entity drift |
| Local vs UI table | `upload_claude_bundles_ui.py --status` | Slugs missing on claude.ai, extra UI slugs, invalid bundles |
| Upload dry-run | `--all --dry-run` | Which files would be staged for upload |

**Future improvement:** content-hash diff (regen bundle hash vs last-sync manifest) —
not implemented yet; use `--replace` after substantive SOT edits until then.

## Code map

| Path | Role |
|---|---|
| `scripts/cortex/gen_claude_bundles.py` | Render + validate local bundles |
| `scripts/cortex/upload_claude_bundles_ui.py` | CLI: `--status`, upload, `--prepare` |
| `libs/claude_bundles/skills_ui*.py` | Playwright CDP automation |
| `libs/claude_bundles/resolver.py` | `CLAUDE_BUNDLE_SLUGS` authority |
| `libs/claude_bundles/bundle_description.py` | 200-char + claude.ai description rules |

## Troubleshooting

- **Replace confirm dialog** — normal for name collisions; automation clicks *Upload and replace*.
- **Description XML tags** — no `<…>` in YAML description (claude.ai rejects). Fix SOT / regen.
- **Single-word slug not detected** — fixed in `skills_ui_panel._SLUG_RE` (2026-07).
- **Manual delete** — remove retired/experimental skills from UI (automation skips, doesn't delete).

## When to run

- After adding/removing a slug in `resolver.py`
- After compressing or editing agent-skill SOT bodies
- Periodic hygiene (monthly or before major seat parity checks)
