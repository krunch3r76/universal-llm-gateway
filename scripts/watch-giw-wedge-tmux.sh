#!/usr/bin/env bash
# Launch watch-giw-wedge-stackdump.py in a dedicated tmux window (session 0).
set -euo pipefail
REPO="/mnt/torus/projects/universal-llm-gateway"
PYTHON="${HOME}/.venvs/universal/bin/python"
SESSION="${TMUX_SESSION:-0}"
WIN_NAME="${GIW_WEDGE_WATCH_WINDOW:-giw-wedge}"

if ! tmux has-session -t "${SESSION}" 2>/dev/null; then
  echo "tmux session ${SESSION} not found" >&2
  exit 1
fi

watch_args=(scripts/watch-giw-wedge-stackdump.py "$@")

if tmux list-windows -t "${SESSION}" -F '#{window_name}' | grep -qx "${WIN_NAME}"; then
  target="${SESSION}:${WIN_NAME}"
  tmux send-keys -t "${target}" C-c "" 2>/dev/null || true
  sleep 0.3
else
  tmux new-window -t "${SESSION}:" -n "${WIN_NAME}"
  target="${SESSION}:${WIN_NAME}"
fi

tmux send-keys -t "${target}" "cd ${REPO}" Enter
quoted_args=""
for arg in "${watch_args[@]}"; do
  quoted_args+=$(printf '%q ' "$arg")
done
tmux send-keys -t "${target}" "${PYTHON} ${quoted_args}" Enter

echo "watching in tmux ${target}"
