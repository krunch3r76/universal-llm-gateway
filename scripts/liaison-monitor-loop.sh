#!/usr/bin/env bash
# Attended monitor for a detached liaison --loop (a:38446).
#
# Touches ``--heartbeat`` while ``tail -n +1 -F``'ing the loop log. The agent
# Shell runs THIS script (notify_on_output ^AGENT_LOOP_TICK_liaison), not the
# python loop. When this Shell dies (tab close / hop retire of the monitor),
# the heartbeat goes stale and liaison-tick releases the seat lock (B1).
#
# Usage:
#   scripts/liaison-monitor-loop.sh --log PATH --heartbeat PATH
#   # optional: --touch-seconds 15

set -euo pipefail

log=""
heartbeat=""
touch_s=15

usage() {
  sed -n '2,14p' "$0"
  exit 2
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --log) log="${2:-}"; shift 2 ;;
    --heartbeat) heartbeat="${2:-}"; shift 2 ;;
    --touch-seconds) touch_s="${2:-}"; shift 2 ;;
    -h|--help) usage ;;
    *) echo "liaison-monitor-loop: unknown arg: $1" >&2; usage ;;
  esac
done

if [[ -z "$log" || -z "$heartbeat" ]]; then
  echo "liaison-monitor-loop: --log and --heartbeat are required" >&2
  exit 2
fi
if [[ ! -f "$log" ]]; then
  echo "liaison-monitor-loop: log missing: $log" >&2
  exit 1
fi

touch "$heartbeat"
(
  while true; do
    touch "$heartbeat" 2>/dev/null || true
    sleep "$touch_s"
  done
) &
toucher=$!
cleanup() {
  kill "$toucher" 2>/dev/null || true
}
trap cleanup EXIT

# -n +1 keeps lines already in the log so a tick between arm and attach is not dropped (N3).
exec tail -n +1 -F "$log"
