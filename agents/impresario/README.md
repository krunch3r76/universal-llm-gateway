# impresario (Agent SDK)

Install: `npm install -g @grok/agent-sdk@0.3.0` (Node ≥22.13).

Validate: `agent-sdk validate --dir agents/impresario`

Run one turn (fixtures, no live MCP): `ULG_FIXTURE_DIR=evals/fixtures agent-sdk run --dir agents/impresario --message "Handle turn.json" --json`

Smoke eval: `agent-sdk eval --dir agents/impresario --tag smoke`

Env: `ULG_IMPRESARIO_LANE` — agent-bus thread id for the impresario lane (default `20908`).

Env: `ULG_LIVE_STEER` — set to `1` to allow live `team_dispatch` generate/steer (default `0` fixture dry-run).
