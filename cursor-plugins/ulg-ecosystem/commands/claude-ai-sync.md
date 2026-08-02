Keep **claude.ai Customize → Skills** aligned with local `CLAUDE_BUNDLE_SLUGS`.

**Runbook:** `docs/agent-guides/skills/claude-ai-bundle-sync.md`  
**Cortex:** `decision:claude-ai-skill-upload-automation`  
**Authority:** `libs/claude_bundles/resolver.py` → `CLAUDE_BUNDLE_SLUGS`

## Subcommands

| Invocation | Action |
|---|---|
| `/claude-ai-sync` | Default: **status** — regen check + UI parity scan |
| `/claude-ai-sync status` | Drift scan only (Chrome CDP required) |
| `/claude-ai-sync regen` | Render shared_sync to `/mnt/torus/gateway/claude-ai-sync/skills/` + validate |
| `/claude-ai-sync upload` | Upload **NEW** slugs (skip table hits) |
| `/claude-ai-sync replace` | Re-upload **all** slugs (content refresh) |

## Prerequisites

Operator must have Chrome attached (logged into claude.ai, Customize → Skills open):

```bash
python scripts/cortex/upload_claude_bundles_ui.py --print-chrome-cmd
# run printed google-chrome … command on Jupiter (or local), then open Skills
```

If CDP is unavailable, run **regen** steps only and report that `--status`/upload need Chrome.

## Agent sequence

### 1. Orient

Read `docs/agent-guides/skills/claude-ai-bundle-sync.md` (md_read first section if long).

### 2. Regen (status, regen, upload, replace)

From repo root `/mnt/torus/projects/universal-llm-gateway`:

```bash
python scripts/cortex/gen_claude_bundles.py
python scripts/cortex/gen_claude_bundles.py --check
```

Stop on `--check` failure; fix SOT/description issues before upload.

### 3. Status (default, status)

```bash
python scripts/cortex/upload_claude_bundles_ui.py --status
```

Interpret stderr:

- `missing_on_ui` → run **upload**
- `invalid_local` → fix regen, re-run **regen**
- `extra_on_ui` → optional manual delete on claude.ai (automation does not delete)
- exit **0** → parity OK

Dry-run preview (optional):

```bash
python scripts/cortex/upload_claude_bundles_ui.py --all --dry-run
```

### 4. Upload (upload)

**Operator runs** (CDP + logged-in session — agent may propose, not assume Chrome):

```bash
python scripts/cortex/upload_claude_bundles_ui.py --all --continue-on-error
```

Re-run `--status` after operator confirms upload finished.

### 5. Replace (replace)

After substantive SOT/bundle content changes:

```bash
python scripts/cortex/upload_claude_bundles_ui.py --all --replace --continue-on-error
```

## Invariants

- **UI path only** — Skills API (`upload_claude_bundles.py --api`) does **not** populate Customize → Skills.
- Descriptions ≤200 chars; no XML-like tags in YAML description.
- Cursor-only skills (e.g. `hei-application-discipline`) are **not** in `CLAUDE_BUNDLE_SLUGS`.
- Success = slug visible in Skills table (`--status` clean), not merely modal closed.

## Report

Close with: target count, on_ui count, missing/extra/invalid lists, next recommended subcommand.
