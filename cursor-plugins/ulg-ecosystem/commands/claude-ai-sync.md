Keep **claude.ai Customize → Skills** a 1:1 mirror of catalog Claude.ai targets.

**How a Cursor agent finds the script:** this command (`/claude-ai-sync`) and
skill `claude-ai-bundle-sync` both name the only entry:
`scripts/cortex/claude-ai-sync-jupiter`. Remote seats SSH from that wrapper;
do not call `upload_claude_bundles_ui.py` on a machine without Jupiter CDP.

**Skill:** `.cursor/skills/claude-ai-bundle-sync/SKILL.md`  
**Cortex:** `decision:claude-ai-skill-upload-automation`  
**Authority:** `config/skills.yaml` → `claude_ai_targets()`  
**CDP host:** Jupiter. Every status / preflight / diagnose / dump / recon /
upload / uninstall goes through `scripts/cortex/claude-ai-sync-jupiter`.

## Subcommands

| Invocation | Action |
|---|---|
| `/claude-ai-sync` | Default: regen check + **recon** (dump + 1:1 apply) |
| `/claude-ai-sync recon` | Dump `/mnt/skills`, then uninstall extras / upload missing / replace stale |
| `/claude-ai-sync dump-skills` | Dump only (`tmp/reviews/claude-skills-latest.zip`) |
| `/claude-ai-sync status` | Playwright **name** scrape (presence/extras — not body bytes) |
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

### 3. Recon (dispatch chat zip — not table-first)

Ordinary `/chat/` code-exec is the standing dump. CSE is fallback only.
`/v1/chat/completions` artifact grab is untested.

```bash
scripts/cortex/claude-ai-sync-jupiter recon
scripts/cortex/claude-ai-sync-jupiter recon --dry-run
```

`recon` applies the catalog 1:1 **library** mirror: uninstall `extra_in_user` (not stock),
upload `missing_from_user`, replace `stale_content`. Stock copies under
`user/` (`docx`, `import-memory`, …) are not extras.
Zip `mirrored=true` is container `skills/user/` vs staged bodies — **not**
proof Cowork `+` → Skills can attach (friction a:30502).

Playwright table scrape is presence-only:

```bash
scripts/cortex/claude-ai-sync-jupiter status
```

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
- Success = slug visible in Customize **Skills table** (`status` clean) plus network oracle on replace — not plugin MATCH, not Stargate `running`, and **not** Cowork `+` → Skills attach (zip/`mirrored` is library-only; a:30502).

## Report

Close with: target count, on_ui count, missing/extra/invalid lists, preflight
path if failed, next recommended subcommand.
