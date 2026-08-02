# ulg-ecosystem — Cursor plugin

User-scoped Cursor plugin that delivers **shared ecosystem skills + commands + `_ulg` rules** to every workspace under `/mnt/torus/projects/` (and any other root on this machine).

**Invariant:** this plugin is the **sole Cursor discovery** surface for census skills and `_ulg` rules. Do **not** also keep those under hub `.cursor/skills/`, hub `.claude/skills/` (census), or parent `projects/.cursor/rules/*_ulg.mdc` — that duplexes the picker / always-on rules. Shared_sync Customize Skills renders land out-of-tree at `/mnt/torus/gateway/claude-ai-sync/skills/` (`gen_claude_bundles.py`); `life_local` SOT may remain under `.claude/skills/`.

## Install

From the ULG repo:

```bash
./scripts/cursor/install-ecosystem-plugin.sh
```

Cursor seat runs install after SoT edits (**¬** ask the operator). Reload Window
only if the IDE picker is still stale after install.

Confirm: **Settings → Plugins → Installed** lists `ulg-ecosystem`.

## What it ships

| Component | Source (SoT) |
|---|---|
| Skills | `SKILLS_CENSUS.txt` → `cursor-plugins/ulg-ecosystem/skills/<slug>/` |
| Commands | `cursor-plugins/ulg-ecosystem/commands/*.md` |
| Rules | `RULES_ULG_CENSUS.txt` → `cursor-plugins/ulg-ecosystem/rules/*_ulg.mdc` |
| Hooks | `sessionStart` → `scripts/verify-ulg-libs.sh` |
| Drift guard | `scripts/cursor/verify-ecosystem-no-duplex.sh` (install fails on duplex) |

## PYTHONPATH / venv pin

**∀ satellite:** `$HOME/.venvs/universal` only (¬ private project venv).
`sitecustomize.py` injects ULG `libs/`.

Pin files (`.vscode/settings.json`, `.envrc`) are **templates** under `templates/`,
synced into every name in `SATELLITES.txt` on each install — continual drift correction.

| Change | Action |
|---|---|
| New satellite | Add name to `SATELLITES.txt` → re-run install |
| Retire satellite | Remove name from `SATELLITES.txt` → re-run install |
| Change pin path | Edit `templates/` → re-run install (overwrites all listed sats) |

## MCP

`vortex-code` stays in `~/.cursor/mcp.json` (user-global). Not duplicated into this plugin's `mcp.json` (avoids double-registration).

## Update story

Edit skill / `_ulg` rule / shared command SoT under this plugin tree, then re-run
`install-ecosystem-plugin.sh` and Reload Window. Install refuses to proceed if
shared material was re-added under hub `.cursor`/`.claude` skills, parent
`*_ulg` rules, or parent/hub shared commands (duplex guard).

## Retired delivery

Hardlinked `{satellite}/.cursor/commands/` and parent `projects/.cursor/skills/` shims are retired. Parent `projects/.cursor/rules/*_ulg.mdc` copies are retired (plugin-only). See `projects/.cursor/shared-workspace-setup.md`.

## Suffix convention

| Suffix | Meaning |
|---|---|
| `_ulg` | Shared across ecosystem (this plugin) |
| `_ws` | Local to this checkout only |
