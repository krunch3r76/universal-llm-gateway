#!/usr/bin/env bash
# Poll path-sim harvest over agent-bus UDS; page operator on HARVEST_READY.
set -euo pipefail
REPO="/mnt/torus/projects/universal-llm-gateway"
PYTHON="${HOME}/.venvs/universal/bin/python"
SESSION="${TMUX_SESSION:-0}"
WIN_NAME="${PATH_SIM_WATCH_WINDOW:-path-sim-529-watch}"

if ! tmux has-session -t "${SESSION}" 2>/dev/null; then
  echo "tmux session ${SESSION} not found" >&2
  exit 1
fi

watch_args=(
  scripts/watch-path-sim-harvest.py
  --phase R
  --thread 6395
  --after-turn 5
  --from-agent cdp
  --page
  --friction-id 27095
  "$@"
)

if tmux list-windows -t "${SESSION}" -F '#{window_name}' | grep -qx "${WIN_NAME}"; then
  target="${SESSION}:${WIN_NAME}"
  tmux send-keys -t "${target}" C-c "" 2>/dev/null || true
else
  tmux new-window -t "${SESSION}:" -n "${WIN_NAME}"
  target="${SESSION}:${WIN_NAME}"
fi

tmux send-keys -t "${target}" "cd ${REPO}" Enter
quoted_args=""
for arg in "${watch_args[@]}"; do
  quoted_args+=$(printf '%q ' "$arg")
done
tmux send-keys -t "${target}" "${PYTHON} ${quoted_args}; echo; echo 'harvest watch done — enter to close'; read" Enter

echo "path-sim 529 harvest watch in tmux ${target}"
