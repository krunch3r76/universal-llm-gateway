#!/usr/bin/env bash
# Optional tmux helper for watch-bus-consult-and-page.py.
# SoT for when/how to arm: runbook:bus-consult-watcher
# Prefer: IDE Cursor terminal (primary) or tmux split-pane in session 0.
# Default here: window named consult-watch in session 0.
# CONSULT_WATCH_PANE=1 → split a pane under 0:0.0 (operator bind 2026-09-03).
# Do NOT mint a detached watch-* session.
set -euo pipefail
REPO="/mnt/torus/projects/universal-llm-gateway"
PYTHON="${HOME}/.venvs/universal/bin/python"
SESSION="${TMUX_SESSION:-0}"
WIN_NAME="${CONSULT_WATCH_WINDOW:-consult-watch}"

if ! tmux has-session -t "${SESSION}" 2>/dev/null; then
  echo "tmux session ${SESSION} not found" >&2
  echo "Prefer IDE terminal per runbook:bus-consult-watcher" >&2
  exit 1
fi

if [[ "${CONSULT_WATCH_PANE:-0}" =~ ^(1|true|yes)$ ]]; then
  target="${SESSION}:0.0"
  quoted_args=""
  for arg in "$@"; do
    quoted_args+=$(printf '%q ' "$arg")
  done
  tmux split-window -t "${target}" -v -c "${REPO}" \
    "${PYTHON} scripts/watch-bus-consult-and-page.py ${quoted_args}"
  echo "watching in tmux pane under ${target} (runbook:bus-consult-watcher)"
  exit 0
fi

watch_args=(scripts/watch-bus-consult-and-page.py "$@")

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

echo "watching in tmux ${target} (prefer CONSULT_WATCH_PANE=1; SoT runbook:bus-consult-watcher)"
