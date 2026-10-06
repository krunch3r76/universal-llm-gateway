#!/usr/bin/env bash
# Detach attended liaison --loop from the Cursor agent Shell (a:38446).
#
# Agent Shell must not BE the loop PID: SIGTERM/harvest exit of a Shell-owned
# loop is a system_notification wake that displaces LOCKED harvest SMS
# (specimen agent-bus:15420 after #115).
#
# Pattern (same substrate as watch-supervise start):
#   1. This script setsid+nohup's liaison-tick.py --loop, writes pid+log, exits.
#   2. The IDE arms a separate monitored Shell: ``tail -n0 -F <log>`` with
#      notify_on_output pattern ^AGENT_LOOP_TICK_liaison.
# Killing the python loop does not exit that tail, so harvest gets no
# loop-status-only abort wake.
#
# Usage:
#   scripts/liaison-arm-loop.sh \
#     --root R --register attended --holder ide:<transcript_id> \
#     [--poll 60] [--heartbeat 1200] [--take-over]
#
# Stdout (last line): JSON {ok, pid, log, pid_file, root, holder, ...}
# Tail recipe is printed on stderr for the seat to copy into Shell.

set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
WATCH_DIR="${WATCH_DIR:-$REPO/tmp/watchers}"
UNIVERSAL_PYTHON="${HOME}/.venvs/universal/bin/python"
mkdir -p "$WATCH_DIR"

root=""
register="attended"
holder=""
poll=60
heartbeat=1200
take_over=0

usage() {
  sed -n '2,22p' "$0"
  exit 2
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --root) root="${2:-}"; shift 2 ;;
    --register) register="${2:-}"; shift 2 ;;
    --holder) holder="${2:-}"; shift 2 ;;
    --poll) poll="${2:-}"; shift 2 ;;
    --heartbeat) heartbeat="${2:-}"; shift 2 ;;
    --take-over) take_over=1; shift ;;
    -h|--help) usage ;;
    *) echo "liaison-arm-loop: unknown arg: $1" >&2; usage ;;
  esac
done

if [[ -z "$root" || -z "$holder" ]]; then
  echo "liaison-arm-loop: --root and --holder are required" >&2
  exit 2
fi
if [[ ! -x "$UNIVERSAL_PYTHON" ]]; then
  echo "liaison-arm-loop: universal venv python missing: $UNIVERSAL_PYTHON" >&2
  exit 1
fi

safe="${root//[^A-Za-z0-9._-]/_}"
log_file="$WATCH_DIR/liaison-loop-${safe}.log"
pid_file="$WATCH_DIR/liaison-loop-${safe}.pid"

# One attended loop per root: stop a prior detach before re-arm.
if [[ -f "$pid_file" ]]; then
  old_pid="$(cat "$pid_file" 2>/dev/null || true)"
  if [[ -n "${old_pid:-}" ]] && kill -0 "$old_pid" 2>/dev/null; then
    kill -TERM "$old_pid" 2>/dev/null || true
    # Brief wait so the lock release lands before the new claim.
    for _ in 1 2 3 4 5 6 7 8 9 10; do
      kill -0 "$old_pid" 2>/dev/null || break
      sleep 0.1
    done
    if kill -0 "$old_pid" 2>/dev/null; then
      kill -KILL "$old_pid" 2>/dev/null || true
    fi
  fi
  rm -f "$pid_file"
fi

: >"$log_file"
argv=(
  "$UNIVERSAL_PYTHON" "$REPO/scripts/liaison-tick.py"
  --root "$root"
  --register "$register"
  --holder "$holder"
  --loop
  --poll "$poll"
  --heartbeat "$heartbeat"
)
if [[ "$take_over" -eq 1 ]]; then
  argv+=(--take-over)
fi

(
  cd "$REPO"
  setsid nohup "${argv[@]}" >>"$log_file" 2>&1 &
  echo $! >"$pid_file"
)

new_pid="$(cat "$pid_file")"
waited=0
armed=0
refused=0
while [[ "$waited" -lt 50 ]]; do
  if grep -q '"loop": "armed"' "$log_file" 2>/dev/null; then
    armed=1
    break
  fi
  if grep -q '"loop": "refused"' "$log_file" 2>/dev/null; then
    refused=1
    break
  fi
  if ! kill -0 "$new_pid" 2>/dev/null; then
    break
  fi
  sleep 0.1
  waited=$((waited + 1))
done

if [[ "$refused" -eq 1 ]]; then
  # Process may still be exiting; do not leave a dead pidfile as "live".
  if ! kill -0 "$new_pid" 2>/dev/null; then
    rm -f "$pid_file"
  fi
  echo "tail: tail -n0 -F $log_file" >&2
  "$UNIVERSAL_PYTHON" -c "
import json
print(json.dumps({
    'ok': False,
    'loop': 'refused',
    'pid': int('$new_pid') if '$new_pid'.isdigit() else None,
    'log': '$log_file',
    'pid_file': '$pid_file',
    'root': '$root',
    'holder': '$holder',
}))
"
  exit 3
fi

if [[ "$armed" -ne 1 ]] || ! kill -0 "$new_pid" 2>/dev/null; then
  echo "liaison-arm-loop: process did not arm; log:" >&2
  tail -n 40 "$log_file" >&2 || true
  rm -f "$pid_file"
  echo "tail: tail -n0 -F $log_file" >&2
  "$UNIVERSAL_PYTHON" -c "
import json
print(json.dumps({
    'ok': False,
    'loop': 'arm_failed',
    'pid': int('$new_pid') if '$new_pid'.isdigit() else None,
    'log': '$log_file',
    'pid_file': '$pid_file',
    'root': '$root',
    'holder': '$holder',
}))
"
  exit 1
fi

echo "tail: tail -n0 -F $log_file" >&2
"$UNIVERSAL_PYTHON" -c "
import json
print(json.dumps({
    'ok': True,
    'loop': 'armed',
    'pid': int('$new_pid'),
    'log': '$log_file',
    'pid_file': '$pid_file',
    'root': '$root',
    'holder': '$holder',
    'poll_s': int('$poll'),
    'heartbeat_s': int('$heartbeat'),
}))
"
exit 0
