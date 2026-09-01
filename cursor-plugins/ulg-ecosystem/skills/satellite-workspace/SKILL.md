---
name: satellite-workspace
description: "On opening email-bridge, journal-bridge, or any Cursor satellite under /mnt/torus/projects — ecosystem vs checkout-local, PYTHONPATH→ULG libs, vortex-code, ulg-ecosystem plugin."
trigger_match_terms: ["satellite-workspace", "satellite", "email-bridge", "journal-bridge", "sibling workspace", "PYTHONPATH", "vortex-code", "ecosystem pack", "ulg-ecosystem", "deploy.sh", "satellite restart", "env-refresh"]
---

# Satellite workspace (Cursor IDE)

## Invariants

| # | Bind |
|---|---|
| 1 | **Ecosystem** skills+commands+`_ulg` rules ship via the **`ulg-ecosystem` Cursor plugin** (`~/.cursor/plugins/local/ulg-ecosystem/`) — **sole** Cursor discovery for shared material. |
| 2 | **Checkout-local** surface stays in `{repo}/.cursor/` (`*_ws.mdc` rules, local non-census skills). |
| 3 | **Venv** = `$HOME/.venvs/universal` only (∀ satellite, including claudeburst). sitecustomize injects ULG `libs/`. Agent/Glass shells: always use `$HOME/.venvs/universal/bin/python` — see `python-universal-venv_ulg.mdc`. |
| 4 | **MCP** = user-global `~/.cursor/mcp.json` (`vortex-code` + `vortex-life`). ¬ `{repo}/.cursor/mcp.json` — a workspace MCP file shadows hub tools (`agent_bus`, `fs`, `cortex`, `team_dispatch`). |
| 5 | **Sharing API** = Cursor plugin. **¬** hardlink commands into each satellite; **¬** `config/skills.yaml` as satellite API; **¬** re-add census skills under hub `.cursor/skills/` or `.claude/skills/`; **¬** shared commands under `projects/.cursor/commands/` (install duplex guard fails closed). Shared_sync Customize staging = `/mnt/torus/gateway/claude-ai-sync/skills/`. |
| 6 | **Pin drift** = corrected by re-running `install-ecosystem-plugin.sh` (roster `SATELLITES.txt` + templates). |
| 7 | **Code cascade** = `vortex-code` on a satellite ⇒ full ULG leverage of **census-shipped** skills, including MCP playbooks (`agent-bus-discipline`, `fs`, `lead-seat-boot`, `checkpoint-discipline`, `session-close-kernel`) and cascade (`path-sim`, `consult-posture`, `claude-ai-cdp-navigation`). `config/skills.yaml` `cursor_only` means ¬Claude.ai Customize slug — **not** hub-exclusive. Skill body presence (census) is independent of whether Jupiter `project-ask` is an MCP endpoint yet. |
| 8 | **Runtime recycle** = after a satellite code revision **or env-file edit the process loads**, restart that process in the same turn with env pickup. Bind-mount ≠ live. `--restart` is process-only (env-UNAWARE). Claudeburst: `scripts/deploy.sh --env-refresh --service perps\|bot\|cb` when `~/claudeburst/*.env` changed; `--restart` only when code changed and env did not. Proof: process start after file **and** env mtime. SOT: `restart-drain-discipline` § Code revision. |
| 9 | **Lane-B parity = git-tracking fact, not architecture.** `team_dispatch(seat=cursor-sdk, workspace="{name}", lane="B")` mints a `git worktree add` from that satellite's own history — it reflects **exactly what that satellite's git history holds**, nothing more. A checkout-local `*_ws.mdc` (even `alwaysApply: true`) that is gitignored or simply never `git add`ed is invisible to Lane-B **and** to a fresh clone, identical to hub. This is per-repo git hygiene, not a fixed "satellites lose IDE context" property — most satellites (`journal-bridge`, `agent-bus`, `pajournal`, `cryptax`) track `.cursor/rules/` cleanly and lose nothing. Lane-A (in-place, `cwd` = the live satellite checkout) sees the real filesystem regardless of git status, so a gitignored/untracked rule still attaches there. Reconciled 2026-08-31: claudeburst's `.gitignore` blanket-ignored `.cursor/` (fixed — only `.cursor/mcp.json` needed the narrower ignore per invariant 4), email-bridge's `core_ws.mdc` was simply never committed (fixed), treasure-chest's `core_ws.mdc` was tracked but had no `alwaysApply` frontmatter at all so it likely never attached even in the IDE (fixed). |

## Partition (binary)

| Layer | Suffix / where | Examples |
|---|---|---|
| Ecosystem (`_ulg`) | Plugin only (SoT under `cursor-plugins/ulg-ecosystem/`) | cortex, git-posture, operator-posture, agent-bus commands |
| Checkout-local (`_ws`) | `{repo}/.cursor/rules/*_ws.mdc` | IMAP hosts, env files, Stargate/gateway hub-only |
| Personal-life helpers | `{repo}/scripts.local/` (nested git, gitignored) | ¬ `scripts/`; hub nest is ULG `scripts.local/` |

## Authoring a shared rule

∀ new rule meant for **all** ecosystem workspaces: load `cursor-rule-placement_ulg.mdc`
(author into plugin `rules/*_ulg.mdc` → `RULES_ULG_CENSUS.txt` → reinstall plugin). ¬ `*_ws` for shared.

## Bootstrap (new satellite)

Follow `/mnt/torus/projects/.cursor/shared-workspace-setup.md`:

1. `git init` under `/mnt/torus/projects/{name}`
2. Add `{name}` to `cursor-plugins/ulg-ecosystem/SATELLITES.txt`
3. Run `scripts/cursor/install-ecosystem-plugin.sh` (installs plugin **and** syncs universal-venv pin: `.vscode/settings.json` + `.envrc`)
4. Add `{name}/.cursor/rules/core_ws.mdc` (checkout-local env/structure) **and commit it** — an untracked or gitignored rule never survives a Lane-B dispatch worktree (invariant 9)
5. Optional `{name}/AGENTS.md` pointing at this skill
6. ¬ create a private project venv — use `$HOME/.venvs/universal` only
7. ¬ `{name}/.cursor/mcp.json` — MCP is user-global only

## Agent duty (ULG hub seat)

When minting a satellite: add it to `SATELLITES.txt`, run the install script, add only checkout-local `*_ws` rules. Do not hardlink shared commands. Do not mint a per-satellite venv. Do not add a workspace `mcp.json`.

After editing satellite runtime code **or the env that process loads**: recycle that satellite in the same turn with env pickup (invariant 8). Do not wait for an operator “go live.” `--restart` does not apply an env edit.
