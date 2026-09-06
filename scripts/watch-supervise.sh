#!/usr/bin/env bash
# Detached supervisor for agent-bus watchers (a:32280).
# Owns poller lifetime outside Cursor Shell; IDE seat tails the log.
# Attended wake: notify_on_output on ``stall-pop:`` (see runbook:bus-consult-watcher).
#
# Usage:
#   scripts/watch-supervise.sh start --label L -- <watcher argv...>
#   scripts/watch-supervise.sh status --label L
#   scripts/watch-supervise.sh stop --label L
#   scripts/watch-supervise.sh tail --label L
#
# SoT arm recipe: runbook:bus-consult-watcher

set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
WATCH_DIR="${WATCH_DIR:-$REPO/tmp/watchers}"
UNIVERSAL_PYTHON="${HOME}/.venvs/universal/bin/python"
mkdir -p "$WATCH_DIR"

usage() {
  sed -n '2,13p' "$0"
  exit 2
}

label=""
cmd=""
if [[ $# -lt 1 ]]; then usage; fi
cmd="$1"
shift

while [[ $# -gt 0 ]]; do
  case "$1" in
    --label)
      label="${2:-}"
      shift 2
      ;;
    --)
      shift
      break
      ;;
    -h|--help)
      usage
      ;;
    *)
      break
      ;;
  esac
done

if [[ -z "$label" ]]; then
  echo "watch-supervise: --label required" >&2
  exit 2
fi

# sanitize label to match libs/bus_watch/state.py
safe="$(printf '%s' "$label" | tr -c 'A-Za-z0-9._\n-' '-')"
pid_file="$WATCH_DIR/${safe}.pid"
log_file="$WATCH_DIR/${safe}.log"
state_file="$WATCH_DIR/${safe}.state.json"

pid_alive() {
  local p="$1"
  [[ -n "$p" ]] && kill -0 "$p" 2>/dev/null
}

read_pid() {
  if [[ -f "$pid_file" ]]; then
    tr -d '[:space:]' <"$pid_file"
  fi
}

cmd_status() {
  local p
  p="$(read_pid || true)"
  if pid_alive "${p:-}"; then
    echo "status=running pid=$p label=$safe log=$log_file state=$state_file"
    if [[ -f "$state_file" ]]; then
      echo "--- state ---"
      cat "$state_file"
    fi
    return 0
  fi
  echo "status=stopped label=$safe pid=${p:-none}"
  if [[ -f "$state_file" ]]; then
    echo "--- state ---"
    cat "$state_file"
  fi
  return 1
}

cmd_stop() {
  local p
  p="$(read_pid || true)"
  if pid_alive "${p:-}"; then
    kill "$p" 2>/dev/null || true
    for _ in 1 2 3 4 5; do
      pid_alive "$p" || break
      sleep 0.2
    done
    if pid_alive "$p"; then
      kill -9 "$p" 2>/dev/null || true
    fi
    echo "stopped pid=$p label=$safe"
  else
    echo "already stopped label=$safe"
  fi
  rm -f "$pid_file"
}

cmd_start() {
  if [[ $# -lt 1 ]]; then
    echo "watch-supervise start: watcher argv required after --" >&2
    exit 2
  fi
  local existing
  existing="$(read_pid || true)"
  if pid_alive "${existing:-}"; then
    echo "reuse pid=$existing label=$safe (already running)"
    echo "log=$log_file"
    echo "state=$state_file"
    return 0
  fi
  rm -f "$pid_file"
  : >"$log_file"
  local has_state=0
  for a in "$@"; do
    if [[ "$a" == "--state-file" ]]; then has_state=1; break; fi
  done
  local -a argv=("$@")
  # Bare ``scripts/*.py`` or system python3 ⇒ re-exec under ULG universal venv.
  if [[ ${#argv[@]} -gt 0 && "${argv[0]}" == *.py ]]; then
    if [[ ! -x "$UNIVERSAL_PYTHON" ]]; then
      echo "watch-supervise: universal venv python missing: $UNIVERSAL_PYTHON" >&2
      exit 1
    fi
    argv=("$UNIVERSAL_PYTHON" "${argv[@]}")
  elif [[ ${#argv[@]} -gt 0 && "${argv[0]}" == *python* && "${argv[0]}" != "$UNIVERSAL_PYTHON" ]]; then
    if [[ ! -x "$UNIVERSAL_PYTHON" ]]; then
      echo "watch-supervise: universal venv python missing: $UNIVERSAL_PYTHON" >&2
      exit 1
    fi
    argv[0]="$UNIVERSAL_PYTHON"
  fi
  if [[ $has_state -eq 0 ]]; then
    argv+=(--state-file "$state_file")
  fi
  (
    cd "$REPO"
    setsid nohup "${argv[@]}" >>"$log_file" 2>&1 &
    echo $! >"$pid_file"
  )
  local new_pid
  new_pid="$(read_pid)"
  sleep 0.15
  if ! pid_alive "$new_pid"; then
    echo "watch-supervise: process exited immediately; tail of log:" >&2
    tail -n 40 "$log_file" >&2 || true
    exit 1
  fi
  echo "started pid=$new_pid label=$safe"
  echo "log=$log_file"
  echo "state=$state_file"
  echo "tail: scripts/watch-supervise.sh tail --label $safe"
}

cmd_tail() {
  if [[ ! -f "$log_file" ]]; then
    echo "no log yet: $log_file" >&2
    exit 1
  fi
  exec tail -n +1 -F "$log_file"
}

case "$cmd" in
  start) cmd_start "$@" ;;
  status) cmd_status ;;
  stop) cmd_stop ;;
  tail) cmd_tail ;;
  *) usage ;;
esac
