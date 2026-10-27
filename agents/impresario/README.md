# impresario (Agent SDK)

Install: `npm install -g @grok/agent-sdk@0.3.0` (Node ≥22.13).

Validate: `agent-sdk validate --dir agents/impresario`

## Run (io production shape)

Supervisor path is chosen from `loginctl show-user io`:

| Linger | Supervisor |
|--------|------------|
| `yes` | `systemd --user` unit `ulg-impresario.service` (see `deploy/ulg-impresario.service`) |
| else | `deploy/start.sh` under detached tmux session `ulg-impresario` |

**Start (systemd, Linger=yes on io):**

```bash
install -d -m 700 ~/.config/ulg-impresario
cp deploy/env.example ~/.config/ulg-impresario/env   # fill AGENT_SDK_BEARER_TOKEN; chmod 600
install -d -m 755 ~/.config/systemd/user
cp deploy/ulg-impresario.service ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now ulg-impresario
```

**Stop:** `systemctl --user stop ulg-impresario`

**Status:** `systemctl --user status ulg-impresario`

**Log:** `~/.local/state/ulg-impresario/serve.log` (and journald for the user unit)

**MCP:** host connection `ulg` uses streamable HTTP (`type: http`) to the house ulg-code endpoint (`/mcp/code`), bearer `${ULG_MCP_TOKEN}` — not the stdio bridge (stdio closes after one in-process call).

**HTTP probe:** `curl -s -H "Authorization: Bearer $AGENT_SDK_BEARER_TOKEN" http://127.0.0.1:3100/v1/info`

Env file template: `deploy/env.example`. Optional house secrets: `EnvironmentFile=-~/.gateway/secrets.env` in the unit (never print values).

## Dev / fixtures

Run one turn (fixtures, no live MCP): `ULG_FIXTURE_DIR=evals/fixtures agent-sdk run --dir agents/impresario --message "Handle turn.json" --json`

Smoke eval: `agent-sdk eval --dir agents/impresario --tag smoke`

## Env reference

| Variable | Default | Role |
|----------|---------|------|
| `ULG_IMPRESARIO_LANE` | `20908` | Bus thread for lane traffic |
| `ULG_IMPRESARIO_MAILBOX` | `impresario-sdk` | `to=` address for this seat |
| `ULG_MODEL_TURNS` | `0` | `1` + `CURSOR_API_KEY` enables live model turns |
| `ULG_LIVE_STEER` | `0` | Live `team_dispatch` steer |
| `ULG_BUS_MODE` | `poll` | `push` disables the poll loop |

Tests: `npm run test` · types: `npm run check`
