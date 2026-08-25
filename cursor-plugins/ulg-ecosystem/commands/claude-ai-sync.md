Keep **claude.ai Customize → Skills** aligned with catalog Claude.ai targets.

**Skill:** `.cursor/skills/claude-ai-bundle-sync/SKILL.md`  
**Cortex:** `decision:claude-ai-skill-upload-automation`  
**Authority:** `config/skills.yaml` → `claude_ai_targets()`  
**CDP host:** Jupiter. Every status / preflight / diagnose / upload / uninstall
command goes through `scripts/cortex/claude-ai-sync-jupiter`.

## Subcommands

| Invocation | Action |
|---|---|
| `/claude-ai-sync` | Default: **status** — regen check + UI parity scan |
| `/claude-ai-sync status` | Drift scan only |
| `/claude-ai-sync preflight` | CDP + panel + Add → Upload menuitem (fail-closed) |
| `/claude-ai-sync diagnose` | Menu inventory JSON; never opens the upload dialog |
| `/claude-ai-sync regen` | Render shared_sync + validate |
| `/claude-ai-sync upload` | Upload **NEW** slugs (skip table hits) |
| `/claude-ai-sync replace` | Re-upload **named** slugs (`--slugs SLUG --replace`) |

Fleet-wide `--all --replace` requires `--force-replace-all`. Cursor owns
regen → preflight → replace → status in-session.

## Prerequisites

Chrome CDP on Jupiter (`scripts/cortex/claude-ai-sync-jupiter ensure-chrome`).
If CDP is down, run **regen** only and report that status/upload need Chrome.

## Agent sequence

### 1. Orient

Read `.cursor/skills/claude-ai-bundle-sync/SKILL.md` (md_read first section if long).

### 2. Regen (status, regen, upload, replace)

From repo root `/mnt/torus/projects/universal-llm-gateway`:

```bash
"$HOME/.venvs/universal/bin/python" scripts/cortex/gen_claude_bundles.py
"$HOME/.venvs/universal/bin/python" scripts/cortex/gen_claude_bundles.py --check
```

Stop on `--check` failure; fix SOT/description issues before upload.

### 3. Status

```bash
scripts/cortex/claude-ai-sync-jupiter status
```

- `missing_on_ui` → **upload**
- `invalid_local` → fix regen
- `extra_on_ui` → `uninstall --slugs …`
- exit **0** → parity OK

### 4. Preflight (before any upload)

```bash
scripts/cortex/claude-ai-sync-jupiter preflight
```

Exit 1 writes `preflight.json`. Isolate the menu without uploading:

```bash
scripts/cortex/claude-ai-sync-jupiter diagnose-upload-menu
```

### 5. Upload new slugs

```bash
scripts/cortex/claude-ai-sync-jupiter upload --slugs SLUG --continue-on-error
scripts/cortex/claude-ai-sync-jupiter status
```

### 6. Replace named slugs

After SOT/bundle content changes:

```bash
scripts/cortex/claude-ai-sync-jupiter upload --slugs SLUG --replace
scripts/cortex/claude-ai-sync-jupiter status
```

## Invariants

- **UI path only** — Skills API (`upload_claude_bundles.py --api`) does not populate Customize → Skills.
- Descriptions ≤200 chars; no XML-like tags in YAML description.
- `cursor_only` skills never upload here.
- Success = slug visible in Skills table (`status` clean) plus network oracle on replace — not plugin MATCH and not Stargate `running`.

## Report

Close with: target count, on_ui count, missing/extra/invalid lists, preflight
path if failed, next recommended subcommand.
