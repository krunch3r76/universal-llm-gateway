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

HUB="${LIAISON_HUB_ROOT:-/mnt/torus/projects/universal-llm-gateway}"
WATCH="${WATCH_DIR:-$HUB/tmp/watchers}"
PROC_ROOT="${LIAISON_PROC_ROOT:-/proc}"
SIGNAL_LOG="${LIAISON_SIGNAL_LOG:-/dev/null}"
PYTHON="${LIAISON_PYTHON:-$HOME/.venvs/universal/bin/python}"
UNIT="liaison-ticker-${ROOT}.service"
TICK="$WATCH/liaison-${ROOT}.tick.json"

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

_tick_register_attended() {
  [[ -f "$TICK" ]] || return 1
  TICK="$TICK" "$PYTHON" -c 'import json, os, sys; from pathlib import Path; data=json.loads(Path(os.environ["TICK"]).read_text(encoding="utf-8")); sys.exit(0 if str(data.get("register") or "") == "attended" else 1)' || return 1
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

LOAD="$(
  systemctl --user show -p LoadState --value "$UNIT" 2>/dev/null || true
)"
LOAD="${LOAD//$'\n'/}"

ORPHANS=()
while IFS= read -r line; do
  [[ -n "$line" ]] && ORPHANS+=("$line")
done < <(_spawn_orphan_pids || true)

if [[ "$LOAD" == "loaded" ]]; then
  if ((${#ORPHANS[@]} > 0)); then
    exit 3
  fi
  if [[ "$DRY_RUN" -eq 0 ]]; then
    systemctl --user restart "$UNIT"
  fi
  exit 0
fi

if ((${#ORPHANS[@]} > 1)); then
  exit 3
fi

if ((${#ORPHANS[@]} == 1)); then
  pid="${ORPHANS[0]}"
  if [[ "$DRY_RUN" -eq 0 ]]; then
    echo "SIGTERM ${pid}" >>"$SIGNAL_LOG"
    kill -TERM "$pid" 2>/dev/null || true
    WATCH_DIR="$WATCH" PYTHONPATH="$HUB/libs" "$PYTHON" - <<PY
import os, time
from pathlib import Path
import bus_watch.fable_lock as fl

fl.WATCH_DIR = Path(os.environ["WATCH_DIR"])
deadline = time.time() + 30.0
while time.time() < deadline:
    if fl.claim_ticker_lease("${ROOT}")["ok"]:
        fl.release_ticker_lease("${ROOT}")
        break
    time.sleep(0.05)
else:
    raise SystemExit(1)
PY
    systemctl --user reset-failed "$UNIT" 2>/dev/null || true
    systemd-run --user --unit="liaison-ticker-${ROOT}" --collect \
      --working-directory="$HUB" \
      --setenv="PYTHONPATH=${HUB}/libs" \
      -- "$PYTHON" scripts/liaison-tick.py --root "$ROOT" --loop --spawn-on-wake
  fi
  exit 0
fi

if [[ "$DRY_RUN" -eq 1 ]]; then
  exit 0
fi

systemctl --user reset-failed "$UNIT" 2>/dev/null || true
systemd-run --user --unit="liaison-ticker-${ROOT}" --collect \
  --working-directory="$HUB" \
  --setenv="PYTHONPATH=${HUB}/libs" \
  -- "$PYTHON" scripts/liaison-tick.py --root "$ROOT" --loop --spawn-on-wake
