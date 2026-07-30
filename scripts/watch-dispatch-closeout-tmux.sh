#!/usr/bin/env bash
# Launch watch-dispatch-closeout.py in a dedicated tmux window (session 0).
set -euo pipefail
REPO="/mnt/torus/projects/universal-llm-gateway"
PYTHON="${HOME}/.venvs/universal/bin/python"
SESSION="${TMUX_SESSION:-0}"
WIN_NAME="${DISPATCH_WATCH_WINDOW:-dispatch-watch}"

if ! tmux has-session -t "${SESSION}" 2>/dev/null; then
  echo "tmux session ${SESSION} not found" >&2
  exit 1
fi

watch_args=(scripts/watch-dispatch-closeout.py "$@")
if [[ ${#watch_args[@]} -eq 1 ]]; then
  watch_args+=(--latest)
fi

# Reuse named window if present; otherwise create a new one.
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
tmux send-keys -t "${target}" "${PYTHON} ${quoted_args}; echo; echo 'done — press enter'; read" Enter

echo "watching in tmux ${target}"
