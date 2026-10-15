#!/usr/bin/env bash
# Recycle gear-3 liaison ticker for one continuity root (one verb).
set -euo pipefail

ROOT=""
DRY_RUN=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --root)
      ROOT="${2:-}"
      shift 2
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    *)
      echo "unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

if [[ -z "$ROOT" ]]; then
  echo "--root is required" >&2
  exit 1
fi

if ! [[ "$ROOT" =~ ^[0-9]+$ ]]; then
  echo "invalid --root: must be numeric continuity id" >&2
  exit 1
fi

HUB="${LIAISON_HUB_ROOT:-/mnt/torus/projects/universal-llm-gateway}"
WATCH="${WATCH_DIR:-$HUB/tmp/watchers}"
PROC_ROOT="${LIAISON_PROC_ROOT:-/proc}"
PYTHON="${LIAISON_PYTHON:-$HOME/.venvs/universal/bin/python}"
if [[ ! -x "$PYTHON" ]]; then
  echo "LIAISON_PYTHON not executable: $PYTHON" >&2
  exit 2
fi
UNIT="liaison-ticker-${ROOT}.service"
TICK="$WATCH/liaison-${ROOT}.tick.json"
KILL="${LIAISON_KILL:-kill}"
REAP_TIMEOUT="${LIAISON_REAP_TIMEOUT_S:-30}"

_read_cmdline() {
  local pid="$1"
  tr '\0' ' ' <"${PROC_ROOT}/${pid}/cmdline" 2>/dev/null || true
}

_attended_loop_pids() {
  local out=()
  local d cmd
  for d in "${PROC_ROOT}"/[0-9]*; do
    [[ -d "$d" ]] || continue
    cmd="$(_read_cmdline "${d##*/}")"
    [[ -n "$cmd" ]] || continue
    if [[ "$cmd" == *"--root ${ROOT}"* ]] || [[ "$cmd" == *"--root=${ROOT}"* ]]; then
      :
    else
      continue
    fi
    if [[ "$cmd" == *"--register attended"* ]]; then
      if [[ "$cmd" == *liaison-tick.py* ]] || [[ "$cmd" == *liaison-arm-loop* ]]; then
        out+=("${d##*/}")
      fi
    fi
  done
  printf '%s\n' "${out[@]}"
}

_attended_loop_without_spawn_pids() {
  local out=()
  local d cmd
  for d in "${PROC_ROOT}"/[0-9]*; do
    [[ -d "$d" ]] || continue
    cmd="$(_read_cmdline "${d##*/}")"
    [[ -n "$cmd" ]] || continue
    [[ "$cmd" == *liaison-tick.py* ]] || continue
    [[ "$cmd" == *"--loop"* ]] || continue
    [[ "$cmd" == *"--spawn-on-wake"* ]] && continue
    if [[ "$cmd" == *"--root ${ROOT}"* ]] || [[ "$cmd" == *"--root=${ROOT}"* ]]; then
      out+=("${d##*/}")
    fi
  done
  printf '%s\n' "${out[@]}"
}

_tick_register_attended() {
  [[ -f "$TICK" ]] || return 1
  set +e
  TICK="$TICK" "$PYTHON" -c 'import json, os, sys; from pathlib import Path
try:
    data = json.loads(Path(os.environ["TICK"]).read_text(encoding="utf-8"))
except (OSError, ValueError, json.JSONDecodeError):
    sys.exit(2)
sys.exit(0 if str(data.get("register") or "") == "attended" else 1)'
  local rc=$?
  set -e
  if [[ "$rc" -eq 2 ]]; then
    echo "tick json parse failed: $TICK" >&2
    exit 2
  fi
  return "$rc"
}

_spawn_orphan_pids() {
  local out=()
  local d cmd
  for d in "${PROC_ROOT}"/[0-9]*; do
    [[ -d "$d" ]] || continue
    cmd="$(_read_cmdline "${d##*/}")"
    [[ -n "$cmd" ]] || continue
    [[ "$cmd" == *liaison-tick.py* ]] || continue
    [[ "$cmd" == *"--loop"* ]] || continue
    [[ "$cmd" == *"--spawn-on-wake"* ]] || continue
    [[ "$cmd" == *"--register attended"* ]] && continue
    if [[ "$cmd" == *"--root ${ROOT}"* ]] || [[ "$cmd" == *"--root=${ROOT}"* ]]; then
      out+=("${d##*/}")
    fi
  done
  printf '%s\n' "${out[@]}"
}

if _tick_register_attended; then
  exit 2
fi
if [[ -n "$(_attended_loop_pids | head -1)" ]]; then
  exit 2
fi
if [[ -n "$(_attended_loop_without_spawn_pids | head -1)" ]]; then
  exit 2
fi

LOAD="$(
  systemctl --user show -p LoadState --value "$UNIT" 2>/dev/null || true
)"
LOAD="${LOAD//$'\n'/}"

ORPHANS=()
while IFS= read -r line; do
  [[ -n "$line" ]] && ORPHANS+=("$line")
done < <(_spawn_orphan_pids || true)

if [[ "$LOAD" == "loaded" ]]; then
  MAIN_PID="$(
    systemctl --user show -p MainPID --value "$UNIT" 2>/dev/null || true
  )"
  MAIN_PID="${MAIN_PID//$'\n'/}"
  FILTERED=()
  for o in "${ORPHANS[@]}"; do
    if [[ -n "$MAIN_PID" && "$MAIN_PID" != "0" && "$o" == "$MAIN_PID" ]]; then
      continue
    fi
    FILTERED+=("$o")
  done
  ORPHANS=("${FILTERED[@]}")
  if ((${#ORPHANS[@]} > 0)); then
    echo "loaded unit has orphan ticker pids: ${ORPHANS[*]}" >&2
    exit 3
  fi
  if [[ "$DRY_RUN" -eq 0 ]]; then
    systemctl --user restart "$UNIT"
  fi
  exit 0
fi

if ((${#ORPHANS[@]} > 1)); then
  echo "multiple orphan ticker pids: ${ORPHANS[*]}" >&2
  exit 3
fi

_start_cold() {
  if [[ "$DRY_RUN" -eq 1 ]]; then
    return 0
  fi
  echo "cold_start"
  systemctl --user reset-failed "$UNIT" 2>/dev/null || true
  systemd-run --user --unit="liaison-ticker-${ROOT}" --collect \
    --working-directory="$HUB" \
    --setenv="PYTHONPATH=${HUB}/libs" \
    -- "$PYTHON" scripts/liaison-tick.py --root "$ROOT" --loop --spawn-on-wake
}

if ((${#ORPHANS[@]} == 1)); then
  pid="${ORPHANS[0]}"
  if [[ "$DRY_RUN" -eq 0 ]]; then
    WATCH_DIR="$WATCH" PYTHONPATH="$HUB/libs" ROOT="$ROOT" "$PYTHON" - <<'PY'
import os
from pathlib import Path
import bus_watch.fable_lock as fl

fl.WATCH_DIR = Path(os.environ["WATCH_DIR"])
_ = fl.read_ticker_lock(os.environ["ROOT"])
PY
    export WATCH_DIR="$WATCH"
    export ROOT
    env "${KILL}" -TERM "$pid" 2>/dev/null || true
    if ! WATCH_DIR="$WATCH" PYTHONPATH="$HUB/libs" ROOT="$ROOT" \
      LIAISON_PROC_ROOT="$PROC_ROOT" LIAISON_REAP_TIMEOUT_S="$REAP_TIMEOUT" \
      ORPHAN_PID="$pid" "$PYTHON" - <<'PY'
import os
import sys
import time
from pathlib import Path

import bus_watch.fable_lock as fl

fl.WATCH_DIR = Path(os.environ["WATCH_DIR"])
root = os.environ["ROOT"]
orphan = os.environ["ORPHAN_PID"]
proc_root = Path(os.environ["LIAISON_PROC_ROOT"])
timeout = float(os.environ.get("LIAISON_REAP_TIMEOUT_S", "30"))
deadline = time.time() + timeout
while time.time() < deadline:
    proc_gone = not (proc_root / orphan).is_dir()
    lock = fl.read_ticker_lock(root)
    if proc_gone and not fl.ticker_lease_live(lock, root_id=root):
        sys.exit(0)
    time.sleep(0.05)
print("orphan reap timeout: proc or lease still live", file=sys.stderr)
sys.exit(0)
PY
    then
      echo "orphan branch: post-SIGTERM wait failed" >&2
    fi
    systemctl --user reset-failed "$UNIT" 2>/dev/null || true
    if ! systemd-run --user --unit="liaison-ticker-${ROOT}" --collect \
      --working-directory="$HUB" \
      --setenv="PYTHONPATH=${HUB}/libs" \
      -- "$PYTHON" scripts/liaison-tick.py --root "$ROOT" --loop --spawn-on-wake; then
      echo "orphan branch: systemd-run failed" >&2
      exit 1
    fi
  fi
  exit 0
fi

if [[ "$DRY_RUN" -eq 1 ]]; then
  exit 0
fi

_start_cold
