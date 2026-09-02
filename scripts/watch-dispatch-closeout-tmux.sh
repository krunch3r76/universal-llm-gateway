#!/usr/bin/env bash
# Launch watch-dispatch-closeout.py in a dedicated, attachable tmux session.
#
# Default: detached session named from --label (watch-<label>) or dispatch-watch.
# Legacy: TMUX_SESSION=0 reuses window dispatch-watch inside session 0.
#
# Attach:  tmux attach -t <session>
# Log:     /tmp/<session>.log
set -euo pipefail
REPO="/mnt/torus/projects/universal-llm-gateway"
PYTHON="${HOME}/.venvs/universal/bin/python"
LEGACY_SESSION="${TMUX_SESSION:-}"
WIN_NAME="${DISPATCH_WATCH_WINDOW:-dispatch-watch}"

watch_args=(scripts/watch-dispatch-closeout.py "$@")
if [[ ${#watch_args[@]} -eq 1 ]]; then
  watch_args+=(--latest)
fi

SESSION="${DISPATCH_WATCH_SESSION:-}"
if [[ -z "${SESSION}" && -z "${LEGACY_SESSION}" ]]; then
  label=""
  prev=""
  for arg in "$@"; do
    if [[ "${prev}" == "--label" ]]; then
      label="${arg}"
      break
    fi
    prev="${arg}"
  done
  if [[ -n "${label}" ]]; then
    slug="$(printf '%s' "${label}" | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9' '-')"
    slug="${slug#-}"
    slug="${slug%-}"
    SESSION="watch-${slug}"
  else
    SESSION="dispatch-watch"
  fi
fi

quoted_args=""
for arg in "${watch_args[@]}"; do
  quoted_args+=$(printf '%q ' "$arg")
done

inner="cd ${REPO} && ${PYTHON} ${quoted_args}; echo; echo '--- watcher done ---'; read"

if [[ -n "${LEGACY_SESSION}" ]]; then
  SESSION="${LEGACY_SESSION}"
  if ! tmux has-session -t "${SESSION}" 2>/dev/null; then
    echo "tmux session ${SESSION} not found" >&2
    exit 1
  fi
  if tmux list-windows -t "${SESSION}" -F '#{window_name}' | grep -qx "${WIN_NAME}"; then
    target="${SESSION}:${WIN_NAME}"
    tmux send-keys -t "${target}" C-c "" 2>/dev/null || true
  else
    tmux new-window -t "${SESSION}:" -n "${WIN_NAME}"
    target="${SESSION}:${WIN_NAME}"
  fi
  tmux send-keys -t "${target}" "${inner}" Enter
  echo "watching in tmux ${target}"
  echo "attach: tmux attach -t ${SESSION}"
  exit 0
fi

LOG="/tmp/${SESSION}.log"
if tmux has-session -t "${SESSION}" 2>/dev/null; then
  tmux kill-session -t "${SESSION}"
fi
tmux new-session -d -s "${SESSION}" -n "${WIN_NAME}" \
  "bash -lc $(printf '%q' "${inner} | tee -a ${LOG}")"

echo "watching in tmux session: ${SESSION}"
echo "attach: tmux attach -t ${SESSION}"
echo "log:    ${LOG}"
