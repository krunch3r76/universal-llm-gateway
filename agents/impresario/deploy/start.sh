#!/usr/bin/env bash
# Fallback supervisor when loginctl Linger≠yes (template — not used when systemd user unit is live).
set -euo pipefail

STATE_ROOT="${ULG_IMPRESARIO_STATE_ROOT:-$HOME/.local/state/ulg-impresario}"
LOCK_DIR="$STATE_ROOT/lock"
AGENT_DIR="/mnt/torus/projects/universal-llm-gateway-wt-agent-sdk-impresario-probe/agents/impresario"
ENV_FILE="${ULG_IMPRESARIO_ENV:-$HOME/.config/ulg-impresario/env}"

mkdir -p "$STATE_ROOT" "$LOCK_DIR"
if ! mkdir "$LOCK_DIR/run" 2>/dev/null; then
  echo "ulg-impresario: another serve holds $STATE_ROOT (lock $LOCK_DIR/run)" >&2
  exit 1
fi
trap 'rmdir "$LOCK_DIR/run" 2>/dev/null || true' EXIT

set -a
# shellcheck source=/dev/null
source "$ENV_FILE"
set +a
if [[ -f "$HOME/.gateway/secrets.env" ]]; then
  set -a
  # shellcheck source=/dev/null
  source "$HOME/.gateway/secrets.env"
  set +a
fi

export ULG_IMPRESARIO_STATE_ROOT="$STATE_ROOT"

while true; do
  if ! agent-sdk serve \
    --dir "$AGENT_DIR" \
    --mode single \
    --host 127.0.0.1 \
    --port 3100 \
    --state-root "$STATE_ROOT" \
    --bearer-token "${AGENT_SDK_BEARER_TOKEN:?AGENT_SDK_BEARER_TOKEN unset}" \
    >>"$STATE_ROOT/serve.log" 2>&1; then
    echo "ulg-impresario: serve exited; restarting in 5s" >&2
  fi
  sleep 5
done
