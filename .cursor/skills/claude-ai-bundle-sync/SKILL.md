---
trigger_match_terms: ["claude-ai-bundle-sync", "claude_ai_bundle_sync", "skill", "subsystem", "covering", "claude.ai", "bundle", "generation", "sync", "libs", "claude_bundles", "scripts"]
---

# Claude.ai bundle sync workflow

Operational runbook for keeping **claude.ai Customize → Skills** aligned with
catalog Claude.ai targets (`config/skills.yaml` → `catalog.claude_ai_targets()` =
`shared_sync` ∪ `life_local` with `mcp_surface_required ≠ code`). `cursor_only`
skills never upload here.

**Cursor command:** `/claude-ai-sync` (`.cursor/commands/claude-ai-sync.md`)  
**Jupiter wrapper:** `scripts/cortex/claude-ai-sync-jupiter`  
**Cortex checkpoint:** `decision:claude-ai-skill-upload-automation` · agent-bus thread 4050.

## Cursor owns upload (BINDING — operator 2026-07-26)

`∀ edit(Claude slug) ⇒ cursor regen ∧ upload/replace ∧ verify` in-session.
`¬ ask(operator, /claude-ai-sync ∨ Customize upload ∨ “sync the slug”)`.
Staging under `~/.gateway/claude-ai-sync/` alone is **not** live — UI replace
required. Same class of duty as plugin install after census SoT edits
(`skill-surface_ulg` § Cursor owns sync). the operator is never the standing sync seat.

## SOT authority chain (before any skill edit)

| Layer | Path | Role |
|---|---|---|
| **Catalog** | `config/skills.yaml` | Sole placement authority — desired UI set is derived, ¬ a parallel slug list |
| **SOT (edit)** | `cursor-plugins/ulg-ecosystem/skills/{slug}/` when census · else `.cursor/skills/{slug}/` (shared_sync) · `.claude/skills/{slug}/` (life_local) | Authoritative body — hand-edit SoT only; catalog `resolve_sot` picks path |
| **Entity** | `agent_skill:{slug}` via `source_uri` | Points at SOT; ¬ a parallel body store |
| **Generated** | `/mnt/torus/gateway/claude-ai-sync/skills/{slug}/SKILL.md` (shared_sync only; override `CLAUDE_AI_SKILLS_STAGING`) | `gen_claude_bundles.py` render — **NEVER** hand-edit |
| **UI** | claude.ai Customize → Skills | This runbook: regen → status → upload |

Cross-ref: `agent_skill:skill-document-writing` § SOT authority chain. Cross-ref: `agent_skill:claude-ai-skill-uninstall` for `extra_on_ui` drift. Incident class: patching a shared-sync `.claude/skills` render during an incident creates silent drift until the next regen overwrites it.

## What this is / is not

| Store | Mechanism | Populated by |
|---|---|---|
| **claude.ai Customize → Skills** | UI zip/md upload | `upload_claude_bundles_ui.py` (Playwright + CDP) |
| **Anthropic Skills API** | `POST /v1/skills` | `upload_claude_bundles.py --api` — **wrong store** for UI parity |

**Description length (fleet policy)** (`decision:claude-ai-skill-description-limits-by-surface`):
Cursor SOT, Customize → Skills, and future Skills API inject share one ceiling —
**200** chars (`MAX_SKILL_DESCRIPTION_LEN`). Anthropic API/spec allow 1024; unused
for simplicity. `ingest_skills --check` and bundle `--check` both enforce 200.

## Architecture — two hosts

```
Cursor / remote seat (io, SSH client)
  │  gen_claude_bundles.py  — edit resolver.py, render .claude/skills
  │  claude-ai-sync-jupiter — SSH wrapper (status / upload)
  ▼
Jupiter (DISPLAY=:1, cosmic-comp Wayland)
  │  Chrome CDP 127.0.0.1:9222  — logged-in claude.ai session
  │  upload_claude_bundles_ui.py — Playwright attaches over CDP
  ▼
claude.ai Customize → Skills
```

**Default assumption (2026-07):** Chrome runs on **Jupiter**, not on the Cursor
remote shell. Calling `upload_claude_bundles_ui.py` locally without SSH fails with
`CDP connect failed (127.0.0.1:9222)` when `DISPLAY` is unset.

Cross-ref: `agent-skills/jupiter-browser-via-mcp` (CDP bring-up patterns;
`--remote-allow-origins=*` is required for Playwright WebSocket access).

**MCP connector (not Skills):** connect/rewire claude.ai → toys `/mcp/life` —
Use the `claude-ai-mcp-connect` skill (`restore-connector`, OAuth DCR, dual-endpoint).

## Prerequisites

1. **Repo checkout** at `/mnt/torus/projects/universal-llm-gateway` (NFS — same
   path on Jupiter and the operator workstation).

2. **Python venv** on Jupiter: `~/.venvs/universal` (Playwright installed).

3. **Chrome on Jupiter** with claude.ai logged in — profile
   `~/.gateway/claude-ai-chrome-profile`. The wrapper starts it if CDP is down.

4. **SSH** to <satellite-host>: default `<user>@<satellite-host>` (override with
   `CLAUDE_AI_SSH_USER` / `CLAUDE_AI_SSH_HOST`).

## Routine — regen → scan → upload

### Step 1 — Regen (any host with repo mount)

```bash
cd /mnt/torus/projects/universal-llm-gateway

python scripts/cortex/gen_claude_bundles.py
python scripts/cortex/gen_claude_bundles.py --check
```

- **`OK bundle-descriptions`** — description length (50–200) and no XML tags; this
  is the gate for adding a slug. **200 = fleet SOT ceiling** (Cursor + Customize +
  future API inject; API/spec 1024 unused) —
  `decision:claude-ai-skill-description-limits-by-surface`.
- Full `--check` may exit **1** on unrelated `run_entity_reconcile_check` warnings;
  that does not block upload if bundle-descriptions passed.

> **Note:** `gen_claude_bundles --check` on Jupiter alone may fail cortex-api
> connect if the cortex socket is not available there — regen/check from the
> operator workstation is fine.

### Step 2 — Drift scan (Jupiter)

```bash
scripts/cortex/claude-ai-sync-jupiter status
```

Equivalent manual:

```bash
ssh <user>@<satellite-host> 'bash -s' <<'EOF'
# ensure CDP — see "Bring up Chrome on Jupiter" below if needed
cd /mnt/torus/projects/universal-llm-gateway
BROWSER_CDP_URL=http://127.0.0.1:9222 ~/.venvs/universal/bin/python \
  scripts/cortex/upload_claude_bundles_ui.py --status
EOF
```

Exit code **0** with no `missing_on_ui` lines = full parity. Partial drift is normal
(extra UI-only slugs, unrelated missing slugs).

### Step 3 — Upload NEW slugs (Jupiter)

```bash
# one slug
scripts/cortex/claude-ai-sync-jupiter upload --slugs my-new-skill --continue-on-error

# all *missing* catalog targets (skips slugs already on UI — NOT a replace)
scripts/cortex/claude-ai-sync-jupiter upload --all --continue-on-error
```

### Step 4 — Refresh content for skills already on UI

Prefer **named slugs** after SOT edits. Fleet-wide `--all --replace` is **refused
by default** and requires `--force-replace-all` (blast-radius gate).

```bash
# one / few slugs (default path)
scripts/cortex/claude-ai-sync-jupiter upload --slugs writing-with-provenance --replace

# intentional fleet-wide re-upload only
scripts/cortex/claude-ai-sync-jupiter upload --all --replace --force-replace-all --continue-on-error
```
### Step 5 — Uninstall retired extras (after removing a slug from the catalog)

`--status` reports retired UI leftovers as `extra_on_ui`. Upload cannot delete them.
Automation path (live-verified 2026-07-11):

```bash
# after catalog demotion/removal + regen so claude_ai_targets no longer includes the slug
scripts/cortex/claude-ai-sync-jupiter status
# expect: extra_on_ui lists the retired slug(s)

scripts/cortex/claude-ai-sync-jupiter uninstall --slugs agent-identity-signoff
# UI path encoded in libs/claude_bundles/skills_ui_uninstall.py:
#   row → detail → "More options for {slug}" → Uninstall → confirm Uninstall

scripts/cortex/claude-ai-sync-jupiter status
# expect: OK parity in sync (target=N on_ui=N)
```

Multi-slug: `--slugs a,b,c --continue-on-error`. Menu label is **Uninstall** (not Delete).

### Step 6 — Verify

1. Re-run `scripts/cortex/claude-ai-sync-jupiter status`. Confirm target slug
   absent from `missing_on_ui` and (after uninstall) absent from `extra_on_ui`.
   This is **Customize library** parity only.
2. **Session-loaded skills (BINDING after arc 6895):** scrape the chat UI
   Context → Skills frame — a DOM read, not a model probe. Never treat
   `SKILLS_PROBE_OK` / "do you have the skill?" as verification.

```bash
scripts/cortex/claude-ai-sync-jupiter loaded-skills \
  --chat-url 'https://claude.ai/cowork/cse_…' \
  --require-loaded reasoning-posture
# or: upload_claude_bundles_ui.py --loaded-skills --chat-url … [--json]
```

Helper: `libs/claude_bundles/chat_context_skills.py` (`scrape_loaded_skills`).

## MCP connector restore (toys / <mcp-host>)

When claude.ai shows **Connection expired** for the toys connector, re-auth via
Jupiter Playwright (home-network Chrome — not your VPN laptop):

```bash
scripts/cortex/claude-ai-sync-jupiter restore-connector
```

Exit `restored` = OAuth re-approved; `already_connected` = token still valid server-side.

After an MCP surface change, repair the Claude tool policy without reconnecting:

```bash
scripts/cortex/claude-ai-sync-jupiter set-tool-permissions
```

When both OAuth and permission state need refresh, use the explicit combined path:

```bash
scripts/cortex/claude-ai-sync-jupiter refresh-connector
```

The permission command targets only `toys` → `/mcp/life` → `Other tools`, returns
`changed` or `already_set`, and reload-verifies `Always allow` plus tool
availability.

**Remote VPN (UI still shows dead):** Jupiter OAuth can succeed while your laptop
browser cannot reach `<mcp-host>`. On the remote machine while on VPN:

```bash
# one-time per machine (MCP host LAN IP)
echo '<mcp-host-ip> <mcp-host>' | sudo tee -a /etc/hosts
```

Then hard-refresh claude.ai → Settings → Connectors. Sophos split-DNS / hairpin NAT
is the durable fix; hosts entry is the quick repeat.

Scripts: `scripts/cortex/restore_claude_mcp_connector.py` (connector name
default: `toys`, URL default: `https://<mcp-host>/mcp/life`) and
`scripts/cortex/set_claude_tool_permissions.py` for the permission repair.

## Bring up Chrome on Jupiter

The wrapper `ensure-chrome` step handles this automatically. Manual equivalent:

```bash
ssh <user>@<satellite-host> 'bash -s' <<'EOF'
pkill -f 'remote-debugging-port=9222' 2>/dev/null || true
sleep 1
DISPLAY=:1 nohup google-chrome \
  --remote-debugging-port=9222 \
  --remote-allow-origins=* \
  --user-data-dir="$HOME/.gateway/claude-ai-chrome-profile" \
  --no-first-run \
  --no-default-browser-check \
  > /tmp/chrome-cdp-claude-ai.log 2>&1 &
for i in $(seq 1 15); do
  sleep 1
  curl -sf http://127.0.0.1:9222/json/version >/dev/null && break
done
curl -s http://127.0.0.1:9222/json/version | python3 -c "import sys,json; print(json.load(sys.stdin)['Browser'])"
EOF
```

Then open **Customize → Skills** in that Chrome session if not already on that page.

**Do not** use `/tmp/cdp-profile` for claude.ai sync — that profile is for generic
`browse` fetches; claude.ai login lives in `claude-ai-chrome-profile`.

## Change detection layers

| Layer | Command | Detects |
|---|---|---|
| SOT → bundle render | `gen_claude_bundles.py --check` | Missing SOT, bad descriptions, entity drift |
| Local vs UI table | `claude-ai-sync-jupiter status` | Slugs missing on claude.ai, extra UI slugs |
| Upload dry-run | `… upload --slugs SLUG --dry-run` (or `--all --dry-run` for new-only) | Which files would be staged |

**Future improvement:** content-hash diff (regen bundle hash vs last-sync manifest) —
not implemented yet; use `--slugs … --replace` after substantive SOT edits until then.
(`--all --replace` requires `--force-replace-all`.)

## Code map

| Path | Role |
|---|---|
| `scripts/cortex/claude-ai-sync-jupiter` | SSH wrapper — **default entry for status/upload/uninstall** |
| `scripts/cortex/gen_claude_bundles.py` | Render + validate local bundles |
| `scripts/cortex/upload_claude_bundles_ui.py` | CLI: `--status`, `--uninstall`, upload (run **on Jupiter**) |
| `libs/claude_bundles/skills_ui*.py` | Playwright CDP automation |
| `libs/claude_bundles/skills_ui_uninstall.py` | Uninstall path: More options → Uninstall → confirm |
| `config/skills.yaml` + `libs/claude_bundles/catalog.py` | Sole placement authority; `claude_ai_targets()` |
| `libs/claude_bundles/resolver.py` | Thin catalog facade for bundle consumers |
| `libs/claude_bundles/bundle_description.py` | 200-char + claude.ai description rules |

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `CDP connect failed 127.0.0.1:9222` on Cursor seat | Upload run locally, not on Jupiter | Use `claude-ai-sync-jupiter` |
| `CDP connect failed` on Jupiter | Chrome not running | `claude-ai-sync-jupiter ensure-chrome` |
| `ModuleNotFoundError: universal_logging` on Jupiter | System `sitecustomize` wins; venv hook never injects `libs/` | Wrapper must set `PYTHONPATH=$REPO/libs` (and `PROJECT_ROOT`) |
| Preflight: hash URL but “Skills panel not open” | Hash alone no longer mounts Settings; Customize is a **button**, not a link | `_reopen_skills_from_hash` must click Customize button then Skills |
| WebSocket 403 from Playwright | Missing `--remote-allow-origins=*` | Restart Chrome with flags in runbook |
| `gen_claude_bundles --check` httpx error on Jupiter | Cortex socket not on Jupiter | Run `--check` from workstation |
| Run report `network_status.ok` but upload failed | F9 regex matches Datadog `/api/v2/rum` + bare-2xx slug_echo | Live-verify 2026-07-09 — reopen harden; do not trust F9 until tightened |
| **Replace confirm dialog** | Name collision on UI | Normal — automation clicks *Upload and replace* |
| **Description XML tags** | `<…>` in YAML description | Fix SOT / regen |
| **Retired slug still on UI** | Removed/demoted in `config/skills.yaml` but not uninstalled | `claude-ai-sync-jupiter uninstall --slugs {slug}` (More options → Uninstall) |

## When to run

- After adding/removing/demoting a Claude.ai target row in `config/skills.yaml`
- After compressing or editing shared_sync / life_local SOT bodies
- Periodic hygiene (monthly or before major seat parity checks)
- ¬ after cursor_only SOT edits — those never sync to Customize → Skills

## Implement-packet closeout template

For agent-bus bundle+upload tails, closeout should cite:

1. Catalog row / `surface_class` change (or confirm slug ∈ `claude_ai_targets()`)
2. `OK bundle-descriptions` (or full `--check` output)
3. `--status` **before** (slug in `missing_on_ui`) and **after** (absent)
4. Upload line (`OK {slug}`, `uploaded 1/1`)
5. Skill `content_hash`

Sidecar pattern: `cortex://notes/system/threads/{thread}-bundle-upload-closeout.md`
