#!/usr/bin/env bash
# Arm treasury scout overnight on Jupiter (Grok window on Jupiter display).
# Run from io hub after moving Grok Bot UI to Jupiter.
#
# io keeps: treasury-scout-pickup + treasury-scout-closeout (agent_bus + :8891).
# Jupiter gets: keystroke routine timer → SSH local → grokbot_tab_keystroke.py
#
# Usage:
#   scripts/arm-treasury-jupiter-overnight.sh           # switch timer to Jupiter
#   scripts/arm-treasury-jupiter-overnight.sh --dry-run # preview only

set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${HOME}/.venvs/universal/bin/python"
DRY=0
if [[ "${1:-}" == "--dry-run" ]]; then
  DRY=1
fi

export GROKBOT_SSH_HOST="${GROKBOT_SSH_HOST:-jupiter}"
export GROKBOT_REPO="${GROKBOT_REPO:-/mnt/torus/projects/universal-llm-gateway}"
export GROKBOT_ROUTINE_INTERVAL_S="${GROKBOT_ROUTINE_INTERVAL_S:-600}"

echo "=== treasury jupiter overnight arm ==="
echo "GROKBOT_SSH_HOST=$GROKBOT_SSH_HOST"
echo "GROKBOT_REPO=$GROKBOT_REPO"
echo "interval_s=$GROKBOT_ROUTINE_INTERVAL_S"

if [[ "$DRY" -eq 1 ]]; then
  "$PYTHON" "$REPO/scripts/grokbot-routine-launch.py" launch --dry-run --ssh-host "$GROKBOT_SSH_HOST"
  echo "dry-run only — no timer changes"
  exit 0
fi

echo "--- stop io/orion keystroke timer (if running) ---"
"$REPO/scripts/watch-supervise.sh" stop --label treasury-grokbot-routine 2>/dev/null || true

echo "--- one-shot keystroke smoke on Jupiter ---"
if ! "$PYTHON" "$REPO/scripts/grokbot-routine-launch.py" launch --ssh-host "$GROKBOT_SSH_HOST"; then
  echo "keystroke smoke FAILED — fix Jupiter WAYLAND/display before arming timer" >&2
  exit 1
fi

echo "--- arm interval timer on io (SSH target = Jupiter) ---"
export GROKBOT_SSH_HOST
export GROKBOT_REPO
export GROKBOT_ROUTINE_INTERVAL_S
"$REPO/scripts/watch-supervise.sh" start --label treasury-grokbot-routine -- \
  "$PYTHON" "$REPO/scripts/watch-treasury-grokbot-routine.py"

echo "--- io watchers (must stay up) ---"
"$REPO/scripts/watch-supervise.sh" status --label treasury-scout-pickup || true
"$REPO/scripts/watch-supervise.sh" status --label treasury-scout-closeout || true

echo "DONE — D4 Description paste still required on Grok VM (grok-vm-description-paste.md)"
