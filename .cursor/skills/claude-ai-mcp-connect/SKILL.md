---
name: claude-ai-mcp-connect
description: "Connect or restore claude.ai toys MCP (/mcp/life) — operator restore defaults to refresh-connector (not plain restore); OAuth DCR, permission repair, dual-endpoint."
---

# Claude.ai → toys MCP connect

`connect|rewire|restore(claude.ai MCP) ⇒ Jupiter CDP ∧ life URL ∧ OAuth Approve ∧ verify Connected`.

### Restore routing (BINDING)

Operator asks to **restore / fix / reconnect toys** (incl. "connection expired", tools dead in chat):

| Step | Command |
|---|---|
| **Default (first try)** | `refresh-connector` — forced Disconnect→Connect+OAuth **and** Other-tools permission repair |
| First-time add / legacy `vortex` rename | `restore-connector` only (may Remove→Add custom) |
| After MCP schema/deploy change | `refresh-connector` or `restore-connector --force-reconnect` |

**`already_connected` trap (incident 2026-09-05):** plain `restore-connector` exits `already_connected` when the Customize UI shows **Connected** and no connection-issue copy — even while life chat tools fail. That exit is **not** operator success. If the operator still reports broken toys after `already_connected`, run **`refresh-connector`** (or `--force-reconnect`) and quote fresh `mcp.oauth.token.accepted` events. Tell the operator to **start a new chat** — stale CSE sessions can keep dead tool handles after OAuth refresh.

**Default remote URL:** `https://<mcp-host>/mcp/life` (life surface).
**claude.ai connector display name (SETTLED):** `toys` — product label for what’s available.
**Cursor CallMcpTool / mcp.json ids (BOUND):** `vortex-life` → `/mcp/life`, `vortex-code` → `/mcp/code`. Legacy monolithic `user-vortex` retired.
**If UI still shows `vortex`:** rename or remove+re-add as `toys` → `/mcp/life` (OAuth rules below).
**Cursor coding seats:** `https://<mcp-host>/mcp/code` via `~/.cursor/mcp.json` (`vortex-code`) — ¬ this skill.

### Dual-endpoint invariant (BINDING)

Live mounts are **only** `/mcp/life` and `/mcp/code`. Bare `https://<mcp-host>/mcp` is **defunct** (404 by design after cutover).

- ¬ “fix” connectors/bridges by pointing them at bare `/mcp`
- ¬ treat a bare-`/mcp` 404 as a server outage — it is the cutover signal
- Life (claude.ai `toys`, Cursor `vortex-life`) → `/mcp/life`
- Code (Cursor `vortex-code`, coding seats) → `/mcp/code`
- OAuth resource metadata is **path-scoped**: `/.well-known/oauth-protected-resource/mcp/life` must advertise `resource=…/mcp/life` (never `/mcp/code`)

Cross-ref: `agent-skills/jupiter-browser-via-mcp` · `claude-ai-bundle-sync` (Skills upload; same CDP host).

## When to load

- Operator: "connect claude.ai to MCP", "restore toys connection", "rewire toys connector", "MCP connection expired", "toys not working"
- Dual-endpoint cutover / anyone proposing bare `/mcp` as the live URL
- Toast: `Couldn't register with vortex's sign-in service`

## Architecture

```
Cursor agent
  → scripts/cortex/claude-ai-sync-jupiter ensure-chrome | refresh-connector
  → Jupiter Chrome CDP :9222 (logged-in claude.ai profile)
  → claude.ai Settings → Customize → Connectors → toys
  → <mcp-host> OAuth (/oauth/register DCR ∨ pre-registered client_id=claude-ai)
  → Approve → tools/list on /mcp/life
```

## Happy path (prefer automation)

**Operator restore (default):**

```bash
cd /mnt/torus/projects/universal-llm-gateway
scripts/cortex/claude-ai-sync-jupiter ensure-chrome
scripts/cortex/claude-ai-sync-jupiter refresh-connector \
  --mcp-url 'https://<mcp-host>/mcp/life' --connector-name toys
```

Expect reconnect exit **`restored`** and permissions **`changed`** or **`already_set`**. Verify server-side:
`observability(operation="signal-events", params={"signal":"mcp.oauth*","minutes":5,"limit":10})`
→ fresh `mcp.oauth.token.accepted`, zero `*.rejected`.

**First add / legacy rename / probe-only:**

```bash
scripts/cortex/claude-ai-sync-jupiter restore-connector \
  --mcp-url 'https://<mcp-host>/mcp/life' --connector-name toys
# Mid-rename (UI still labeled vortex): same — script removes vortex and re-adds as toys.
# Or --connector-name vortex to reconnect without renaming.
```

**MCP schema/deploy change only** (Connected UI already good): `set-tool-permissions` alone.

`restore-connector` opens Connectors; if life URL exists under a legacy name (`vortex`) and `--connector-name toys`, **Remove → Add custom** then OAuth. Otherwise Connect/Reconnect + Approve. Returns `restored` | `already_connected` | `renamed_readded`. `--force-reconnect` forces Disconnect then Connect even when Connected. **`refresh-connector`** = `restore-connector --force-reconnect` then `set-tool-permissions`.

`set-tool-permissions` targets only the `toys` life connector's `Other tools`
group. It returns `changed` or `already_set`, and reload-verifies `Always allow`
plus a non-empty tools surface. `refresh-connector` composes forced reconnect
with that permission repair; ordinary `restore-connector` does not broaden
permissions.

∀ automation: `BROWSER_CDP_URL=http://127.0.0.1:9222` on Jupiter; SSH wrapper sets it.

## Preflight (before UI)

| Check | Expect |
|---|---|
| `GET https://<mcp-host>/health` | 200, `deploy_mode` ok |
| `GET …/.well-known/oauth-protected-resource/mcp/life` | `"resource":"https://<mcp-host>/mcp/life"` |
| `POST /mcp/life` unauth | 401 + `WWW-Authenticate` …`resource_metadata="…/mcp/life"` |
| bare `https://<mcp-host>/mcp` | 404 (dual-endpoint cutover) |
| DCR accept-set in **running** container | `docker exec mcp-server grep -n 'token_endpoint_auth_method must be' /app/services/mcp-server/oauth_service.py` → `none or client_secret_post` (not bare `must be none`) |

¬ skip preflight after MCP code/config change — bad metadata/DCR presents as claude.ai toast, not a Playwright bug.  
AS metadata advertising both auth methods **⇏** register path is live — container can lag workspace; verify the running file.

## Add connector (missing vortex)

1. Settings (account profile menu) → Customize → **Connectors** (hash `#settings/customize-connectors`).
2. **Add** → **Add custom connector**.
3. Name `toys` (legacy UI may still say `vortex` — rename to `toys`), URL `https://<mcp-host>/mcp/life`.
4. Advanced (prefer when DCR failed ≥1× this session): OAuth Client ID `claude-ai` + secret from `~/.gateway/mcp.yaml` `oauth.clients` — **skips DCR**.
5. **Add** → OAuth Approve on `<mcp-host>`.

`URL already exists` ⇒ ¬ Add again — open existing row → **Connect**.

## UI automation invariants

| Rule | Why |
|---|---|
| `¬Escape` while Settings modal open | Backdrop closes; hash alone often fails to reopen |
| Open Settings via `[data-testid=user-menu-button]` → **Settings** | Sidebar **Customize** ≠ Connectors settings |
| Scope **Connect** to toys detail (`not connected to toys`) | List page has many popular **Connect** buttons |
| Prefer Playwright `force` click / `expect_page` for OAuth | Same-tab vs popup varies |
| Stale yellow toast ≠ live failure | Confirm with `observability(signal-events, signal=mcp.oauth*)` |

## OAuth config (server)

SOT: `~/.gateway/mcp.yaml` `oauth:` (mounted into mcp-server as `/home/mcp/.gateway`).

| Need | Config |
|---|---|
| claude.ai DCR without Advanced Client ID | `dynamic_client_redirect_hosts` includes `claude.ai`, `claude.com` |
| DCR auth method | Accept `none` **and** `client_secret_post` (claude.ai client picks latter when AS advertises it) |
| Pre-registered fallback | `clients[].client_id: claude-ai` + secret + claude redirect_uris |
| Path-aware resource metadata | `/.well-known/oauth-protected-resource/mcp/life` → resource `/mcp/life` |

After yaml or `services/mcp-server/oauth_*.py` edits: `manage(action=sync_restart, service=mcp)` → `wait_healthy`.

## Diagnose toast `Couldn't register…`

```text
observability(operation="signal-events",
  params={"signal":"mcp.oauth*","minutes":15,"limit":30})
```

| Event | Fix |
|---|---|
| `dynamic.client.rejected` + `redirect_uri host is not allowed` | Add `claude.ai`/`claude.com` to `dynamic_client_redirect_hosts`; restart mcp |
| `dynamic.client.rejected` + `token_endpoint_auth_method must be none` | **1.** `docker exec … grep` accept-set (preflight). Old string / `!= "none"` only → `manage(sync_restart, mcp)` — workspace may already allow both; ¬ re-edit source first. **2.** After live accept includes `client_secret_post`, retry Connect. **3.** Still failing → Advanced `claude-ai` (skip DCR) |
| Resource metadata `resource` = `/mcp/code` while connector is `/mcp/life` | Path-aware `build_protected_resource_metadata` + WWW-Authenticate |
| No Anthropic DCR events after Connect | UI click missed / stale toast — reopen detail, click Connect, re-check events |

## Verify success

| Check | Pass |
|---|---|
| Script exit | `restored` (reconnect leg) — not `already_connected` when operator reported broken |
| OAuth events | `mcp.oauth.token.accepted` within last few minutes; no `*.rejected` |
| Customize UI | URL `…/mcp/life`, **¬** "not connected to toys", **¬** connection-issue toast |
| Chat | New claude.ai chat shows "Used toys integration" after a life tool prompt |

UI **Connected** ⇏ tools work — stale CSE/chat sessions keep dead handles after OAuth refresh; operator must open a **new chat**. Life primaries (`cortex`, `close`, …); coding tools (`manage`, `team_dispatch`) stay on `/mcp/code`.

## Cursor dual bridges (companion)

`~/.cursor/mcp.json`: `vortex-life` → `/mcp/life`, `vortex-code` → `/mcp/code` (stdio → `scripts/mcp-fastmcp-remote-bridge.py`). After edit: reload MCP servers in Cursor.

## Scripts

| Script | Role |
|---|---|
| `scripts/cortex/claude-ai-sync-jupiter` | `ensure-chrome` · `restore-connector` · `set-tool-permissions` · `refresh-connector` |
| `scripts/cortex/restore_claude_mcp_connector.py` | Playwright Connect + Approve |
| `scripts/cortex/set_claude_tool_permissions.py` | Playwright permission repair + reload verification |
| `scripts/mcp-fastmcp-remote-bridge.py` | Cursor stdio→HTTP bridge |

¬ commit `oauth.clients[].client_secret` into skills/docs — read live from `~/.gateway/mcp.yaml`.
