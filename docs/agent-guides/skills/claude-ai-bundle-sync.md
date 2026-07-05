# Claude.ai bundle sync workflow

Operational runbook for keeping **claude.ai Customize → Skills** aligned with
local `CLAUDE_BUNDLE_SLUGS` (~92 skills; matter playbooks retired to case documents).

**Cursor command:** `/claude-ai-sync` (`.cursor/commands/claude-ai-sync.md`)  
**Jupiter wrapper:** `scripts/cortex/claude-ai-sync-jupiter`  
**Cortex checkpoint:** `decision:claude-ai-skill-upload-automation` · agent-bus thread 4050.

## What this is / is not

| Store | Mechanism | Populated by |
|---|---|---|
| **claude.ai Customize → Skills** | UI zip/md upload | `upload_claude_bundles_ui.py` (Playwright + CDP) |
| **Anthropic Skills API** | `POST /v1/skills` | `upload_claude_bundles.py --api` — **wrong store** for UI parity |

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

## Prerequisites

1. **Repo checkout** at `/mnt/torus/projects/universal-llm-gateway` (NFS — same
   path on Jupiter and the operator workstation).

2. **Python venv** on Jupiter: `~/.venvs/universal` (Playwright installed).

3. **Chrome on Jupiter** with claude.ai logged in — profile
   `~/.gateway/claude-ai-chrome-profile`. The wrapper starts it if CDP is down.

4. **SSH** to Jupiter: default `krunch3r@jupiter` (override with
   `CLAUDE_AI_SSH_USER` / `CLAUDE_AI_SSH_HOST`).

## Routine — regen → scan → upload

### Step 1 — Regen (any host with repo mount)

```bash
cd /mnt/torus/projects/universal-llm-gateway

python scripts/cortex/gen_claude_bundles.py
python scripts/cortex/gen_claude_bundles.py --check
```

- **`OK bundle-descriptions`** — description length (50–200) and no XML tags; this
  is the gate for adding a slug.
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
ssh krunch3r@jupiter 'bash -s' <<'EOF'
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

# all missing
scripts/cortex/claude-ai-sync-jupiter upload --all --continue-on-error
```

### Step 4 — Refresh content for skills already on UI

```bash
scripts/cortex/claude-ai-sync-jupiter upload --all --replace --continue-on-error
```

### Step 5 — Verify

Re-run `scripts/cortex/claude-ai-sync-jupiter status`. Confirm target slug absent
from `missing_on_ui`.

## Bring up Chrome on Jupiter

The wrapper `ensure-chrome` step handles this automatically. Manual equivalent:

```bash
ssh krunch3r@jupiter 'bash -s' <<'EOF'
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
| Upload dry-run | `… upload --all --dry-run` | Which files would be staged |

**Future improvement:** content-hash diff (regen bundle hash vs last-sync manifest) —
not implemented yet; use `--replace` after substantive SOT edits until then.

## Code map

| Path | Role |
|---|---|
| `scripts/cortex/claude-ai-sync-jupiter` | SSH wrapper — **default entry for status/upload** |
| `scripts/cortex/gen_claude_bundles.py` | Render + validate local bundles |
| `scripts/cortex/upload_claude_bundles_ui.py` | CLI: `--status`, upload (run **on Jupiter**) |
| `libs/claude_bundles/skills_ui*.py` | Playwright CDP automation |
| `libs/claude_bundles/resolver.py` | `CLAUDE_BUNDLE_SLUGS` authority |
| `libs/claude_bundles/bundle_description.py` | 200-char + claude.ai description rules |

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `CDP connect failed 127.0.0.1:9222` on Cursor seat | Upload run locally, not on Jupiter | Use `claude-ai-sync-jupiter` |
| `CDP connect failed` on Jupiter | Chrome not running | `claude-ai-sync-jupiter ensure-chrome` |
| WebSocket 403 from Playwright | Missing `--remote-allow-origins=*` | Restart Chrome with flags in runbook |
| `gen_claude_bundles --check` httpx error on Jupiter | Cortex socket not on Jupiter | Run `--check` from workstation |
| **Replace confirm dialog** | Name collision on UI | Normal — automation clicks *Upload and replace* |
| **Description XML tags** | `<…>` in YAML description | Fix SOT / regen |
| **Manual delete** | Retired skills on UI | Remove manually — automation skips, doesn't delete |

## When to run

- After adding/removing a slug in `resolver.py`
- After compressing or editing agent-skill SOT bodies
- Periodic hygiene (monthly or before major seat parity checks)

## Implement-packet closeout template

For agent-bus bundle+upload tails, closeout should cite:

1. `resolver.py` diff (one slug line)
2. `OK bundle-descriptions` (or full `--check` output)
3. `--status` **before** (slug in `missing_on_ui`) and **after** (absent)
4. Upload line (`OK {slug}`, `uploaded 1/1`)
5. Skill `content_hash`

Sidecar pattern: `cortex://notes/system/threads/{thread}-bundle-upload-closeout.md`
