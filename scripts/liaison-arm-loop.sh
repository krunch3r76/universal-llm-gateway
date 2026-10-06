#!/usr/bin/env bash
# Detach attended liaison --loop from the Cursor agent Shell (a:38446).
#
# Agent Shell must not BE the loop PID: SIGTERM/harvest exit of a Shell-owned
# loop is a system_notification wake that displaces LOCKED harvest SMS
# (specimen agent-bus:15420 after #115).
#
# Pattern:
#   1. This script setsid+nohup's liaison-tick.py --loop (staging paths until
#      armed), writes pid+log+meta+monitor-heartbeat, exits.
#   2. IDE arms ``scripts/liaison-monitor-loop.sh --log <log> --heartbeat <hb>``
#      with notify_on_output ^AGENT_LOOP_TICK_liaison. That Shell touches the
#      heartbeat; when it dies, the loop releases the seat lock (a:38446 B1).
#
# Re-arm safety (a:38446 B2/B3): pre-kill only same --holder whose /proc cmdline
# still looks like this root's --loop. Foreign holders are left for claim refuse.
#
# Usage:
#   scripts/liaison-arm-loop.sh \
#     --root R --register attended --holder ide:<transcript_id> \
#     [--poll 60] [--heartbeat 1200] [--take-over]
#
# Stdout (last line): JSON {ok, pid, log, pid_file, meta_file, monitor_heartbeat, ...}

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
  sed -n '2,24p' "$0"
  exit 2
}

emit_json() {
  # JSON via argv — never interpolate paths/holders into python source (N6).
  "$UNIVERSAL_PYTHON" - "$@" <<'PY'
import json, sys
print(json.dumps(json.loads(sys.argv[1]), default=str))
PY
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
case "$poll" in *[!0-9]*|"") echo "liaison-arm-loop: --poll must be int" >&2; exit 2 ;; esac
case "$heartbeat" in *[!0-9]*|"") echo "liaison-arm-loop: --heartbeat must be int" >&2; exit 2 ;; esac

safe="${root//[^A-Za-z0-9._-]/_}"
log_file="$WATCH_DIR/liaison-loop-${safe}.log"
pid_file="$WATCH_DIR/liaison-loop-${safe}.pid"
meta_file="$WATCH_DIR/liaison-loop-${safe}.meta.json"
hb_file="$WATCH_DIR/liaison-loop-${safe}.monitor-heartbeat"
attempt_log="$WATCH_DIR/liaison-loop-${safe}.attempt.log"
attempt_pid="$WATCH_DIR/liaison-loop-${safe}.attempt.pid"

cmdline_is_this_loop() {
  local pid="$1"
  local cmd
  cmd="$(tr '\0' ' ' <"/proc/$pid/cmdline" 2>/dev/null || true)"
  [[ "$cmd" == *liaison-tick.py* && "$cmd" == *"--root ${root}"* && "$cmd" == *"--loop"* ]]
}

# Same-holder re-arm only. Foreign holder → leave live loop; claim will refuse (B2).
if [[ -f "$meta_file" ]]; then
  old_holder="$("$UNIVERSAL_PYTHON" - "$meta_file" <<'PY'
import json, sys
from pathlib import Path
p = Path(sys.argv[1])
try:
    print(json.loads(p.read_text(encoding="utf-8")).get("holder") or "")
except (OSError, json.JSONDecodeError):
    print("")
PY
)"
  old_pid="$(
    if [[ -f "$pid_file" ]]; then cat "$pid_file"; fi
  )"
  if [[ "$old_holder" == "$holder" && -n "${old_pid:-}" ]] && kill -0 "$old_pid" 2>/dev/null; then
    if cmdline_is_this_loop "$old_pid"; then
      kill -TERM "$old_pid" 2>/dev/null || true
      for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
        kill -0 "$old_pid" 2>/dev/null || break
        sleep 0.1
      done
      # No SIGKILL — finally must run to release the lock (B2 case 3).
    fi
  fi
elif [[ -f "$pid_file" ]]; then
  # Legacy pid-only file: kill only when cmdline matches this root's loop (B3).
  old_pid="$(cat "$pid_file" 2>/dev/null || true)"
  if [[ -n "${old_pid:-}" ]] && kill -0 "$old_pid" 2>/dev/null && cmdline_is_this_loop "$old_pid"; then
    kill -TERM "$old_pid" 2>/dev/null || true
    for _ in 1 2 3 4 5 6 7 8 9 10; do
      kill -0 "$old_pid" 2>/dev/null || break
      sleep 0.1
    done
  fi
fi

rm -f "$attempt_log" "$attempt_pid"
: >"$attempt_log"
# Heartbeat must exist before the loop's first poll or it exits as monitor_gone (B1).
touch "$hb_file"
argv=(
  "$UNIVERSAL_PYTHON" "$REPO/scripts/liaison-tick.py"
  --root "$root"
  --register "$register"
  --holder "$holder"
  --loop
  --poll "$poll"
  --heartbeat "$heartbeat"
  --monitor-heartbeat "$hb_file"
)
if [[ "$take_over" -eq 1 ]]; then
  argv+=(--take-over)
fi

(
  cd "$REPO"
  # Prefer this checkout's libs/ (worktree or master) over a stale sitecustomize inject.
  export PYTHONPATH="$REPO/libs${PYTHONPATH:+:$PYTHONPATH}"
  setsid nohup env \
    PYTHONPATH="$PYTHONPATH" \
    LIAISON_LOOP_PID_FILE="$pid_file" \
    LIAISON_LOOP_META_FILE="$meta_file" \
    "${argv[@]}" >>"$attempt_log" 2>&1 &
  echo $! >"$attempt_pid"
)

new_pid="$(cat "$attempt_pid")"
waited=0
armed=0
refused=0
while [[ "$waited" -lt 50 ]]; do
  if grep -q '"loop": "armed"' "$attempt_log" 2>/dev/null; then
    armed=1
    break
  fi
  if grep -q '"loop": "refused"' "$attempt_log" 2>/dev/null; then
    refused=1
    break
  fi
  if ! kill -0 "$new_pid" 2>/dev/null; then
    break
  fi
  sleep 0.1
  waited=$((waited + 1))
done

parse_refused() {
  "$UNIVERSAL_PYTHON" - "$attempt_log" <<'PY'
import json, sys
from pathlib import Path
reason, lock = "fable_lock_held", None
for line in Path(sys.argv[1]).read_text(encoding="utf-8", errors="replace").splitlines():
    line = line.strip()
    if not line.startswith("{") or '"loop": "refused"' not in line:
        continue
    try:
        row = json.loads(line)
    except json.JSONDecodeError:
        continue
    reason = str(row.get("reason") or reason)
    lock = row.get("lock")
print(json.dumps({"reason": reason, "lock": lock}))
PY
}

if [[ "$refused" -eq 1 ]]; then
  if ! kill -0 "$new_pid" 2>/dev/null; then
    rm -f "$attempt_pid"
  fi
  refused_blob="$(parse_refused)"
  payload="$("$UNIVERSAL_PYTHON" - "$refused_blob" "$new_pid" "$attempt_log" "$root" "$holder" <<'PY'
import json, sys
extra = json.loads(sys.argv[1])
pid = int(sys.argv[2]) if sys.argv[2].isdigit() else None
print(json.dumps({
    "ok": False,
    "loop": "refused",
    "reason": extra.get("reason"),
    "lock": extra.get("lock"),
    "pid": pid,
    "log": sys.argv[3],
    "root": sys.argv[4],
    "holder": sys.argv[5],
}))
PY
)"
  echo "tail: (do not start — arm refused)" >&2
  emit_json "$payload"
  exit 3
fi

if [[ "$armed" -ne 1 ]] || ! kill -0 "$new_pid" 2>/dev/null; then
  echo "liaison-arm-loop: process did not arm; log:" >&2
  tail -n 40 "$attempt_log" >&2 || true
  rm -f "$attempt_pid"
  payload="$("$UNIVERSAL_PYTHON" - "$new_pid" "$attempt_log" "$root" "$holder" <<'PY'
import json, sys
pid = int(sys.argv[1]) if sys.argv[1].isdigit() else None
print(json.dumps({
    "ok": False,
    "loop": "arm_failed",
    "pid": pid,
    "log": sys.argv[2],
    "root": sys.argv[3],
    "holder": sys.argv[4],
}))
PY
)"
  echo "tail: (do not start — arm failed)" >&2
  emit_json "$payload"
  exit 1
fi

# Promote staging artifacts only after armed (B2).
cp "$attempt_log" "$log_file"
echo "$new_pid" >"$pid_file"
touch "$hb_file"
"$UNIVERSAL_PYTHON" - "$meta_file" "$new_pid" "$holder" "$root" "$log_file" "$hb_file" <<'PY'
import json, sys
from pathlib import Path
Path(sys.argv[1]).write_text(
    json.dumps({
        "pid": int(sys.argv[2]),
        "holder": sys.argv[3],
        "root": sys.argv[4],
        "log": sys.argv[5],
        "monitor_heartbeat": sys.argv[6],
    }, indent=2) + "\n",
    encoding="utf-8",
)
PY
rm -f "$attempt_pid"

monitor_cmd="scripts/liaison-monitor-loop.sh --log $log_file --heartbeat $hb_file"
echo "tail: $monitor_cmd" >&2
payload="$("$UNIVERSAL_PYTHON" - "$new_pid" "$log_file" "$pid_file" "$meta_file" "$hb_file" "$root" "$holder" "$poll" "$heartbeat" <<'PY'
import json, sys
print(json.dumps({
    "ok": True,
    "loop": "armed",
    "pid": int(sys.argv[1]),
    "log": sys.argv[2],
    "pid_file": sys.argv[3],
    "meta_file": sys.argv[4],
    "monitor_heartbeat": sys.argv[5],
    "root": sys.argv[6],
    "holder": sys.argv[7],
    "poll_s": int(sys.argv[8]),
    "heartbeat_s": int(sys.argv[9]),
    "monitor": (
        f"scripts/liaison-monitor-loop.sh --log {sys.argv[2]} "
        f"--heartbeat {sys.argv[5]}"
    ),
}))
PY
)"
emit_json "$payload"
exit 0
