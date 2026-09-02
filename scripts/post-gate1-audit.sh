#!/usr/bin/env bash
# post-gate1-audit.sh — Gate 1 audit same-leg bind (thread 191).
#
# Invariant: post(Gate1_audit, thread=191) on one leg implies
# wake(web-anthropic) + arm_watcher(191) + transition_pager("audit in flight").
# Post without wake + watcher on the same leg is incomplete.
#
# Usage:
#   scripts/post-gate1-audit.sh \
#     --subject "Gate 1 audit: perps risk guard change" \
#     --body-file /tmp/gate1-audit.md
#
#   scripts/post-gate1-audit.sh \
#     --subject "Gate 1 audit: …" \
#     --body-file /tmp/gate1-audit.md \
#     --thread 191 \
#     --watch-label watch-191 \
#     --invoke-cdp-wake
#
# Options:
#   --subject STR        Required audit subject line.
#   --body-file PATH     Required structured audit body (no diff inline).
#   --thread ID          Agent-bus thread (default: 191).
#   --watch-label STR    Tmux watcher label slug (default: watch-191).
#   --invoke-cdp-wake    Attempt CDP wake via Jupiter project-ask escape path.
#   --no-page            Skip transition pager (debug only).
#   --no-watch           Skip arming tmux watcher (debug only).
#
# CDP wake: full product path is team_dispatch(model=cdp/opus-5, …) from a
# code seat with MCP. This script posts the bus audit + wake pointer turn and
# prints the team_dispatch one-liner. With --invoke-cdp-wake it also fires the
# claude-ai-sync-jupiter project-ask escape (SSH to Jupiter).
#
# Doctrine: decision:gate1-audit-implied-movement
# Skill: agent-bus-discipline § Gate 1 audit leg (thread 191)
set -euo pipefail

REPO="/mnt/torus/projects/universal-llm-gateway"
AGENT_BUS="${REPO}/scripts/agent-bus"
EMAIL_BRIDGE_SOCK="${EMAIL_BRIDGE_SOCK:-/tmp/universal-protocol/email-bridge.sock}"

THREAD="191"
WATCH_LABEL="watch-191"
SUBJECT=""
BODY_FILE=""
INVOKE_CDP_WAKE=0
NO_PAGE=0
NO_WATCH=0

usage() {
  sed -n '2,28p' "$0" | sed 's/^# \{0,1\}//'
  exit "${1:-0}"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --subject)
      SUBJECT="${2:?--subject requires value}"
      shift 2
      ;;
    --body-file)
      BODY_FILE="${2:?--body-file requires value}"
      shift 2
      ;;
    --thread)
      THREAD="${2:?--thread requires value}"
      shift 2
      ;;
    --watch-label)
      WATCH_LABEL="${2:?--watch-label requires value}"
      shift 2
      ;;
    --invoke-cdp-wake)
      INVOKE_CDP_WAKE=1
      shift
      ;;
    --no-page)
      NO_PAGE=1
      shift
      ;;
    --no-watch)
      NO_WATCH=1
      shift
      ;;
    -h|--help)
      usage 0
      ;;
    *)
      echo "unknown arg: $1" >&2
      usage 1
      ;;
  esac
done

if [[ -z "${SUBJECT}" || -z "${BODY_FILE}" ]]; then
  echo "error: --subject and --body-file are required" >&2
  usage 1
fi
if [[ ! -f "${BODY_FILE}" ]]; then
  echo "error: body file not found: ${BODY_FILE}" >&2
  exit 1
fi

cd "${REPO}"

# --- 1. Resolve after_turn from thread tip ---
fetch_json="$("${AGENT_BUS}" fetch --thread "${THREAD}" --last 1 --compact 2>/dev/null || true)"
AFTER_TURN="$(printf '%s' "${fetch_json}" | "${HOME}/.venvs/universal/bin/python" -c '
import json, sys
raw = sys.stdin.read().strip()
if not raw:
    print(0)
    raise SystemExit
data = json.loads(raw)
turns = data.get("turns") or []
if not turns:
    print(0)
else:
    print(int(turns[0].get("turn_number") or 0))
' 2>/dev/null || echo 0)"

echo "thread=${THREAD} after_turn=${AFTER_TURN}"

# --- 2. Post structured audit to thread 191 ---
post_json="$("${AGENT_BUS}" reply \
  --thread "${THREAD}" \
  --to web-anthropic \
  --from-agent cursor \
  --subject "${SUBJECT}" \
  --body-file "${BODY_FILE}" \
  --after-turn "${AFTER_TURN}")"

AUDIT_TURN="$(printf '%s' "${post_json}" | "${HOME}/.venvs/universal/bin/python" -c '
import json, sys
data = json.loads(sys.stdin.read())
print(int(data.get("turn_number") or data.get("turn") or 0))
')"

echo "posted audit turn=${AUDIT_TURN}"
echo "${post_json}" | "${HOME}/.venvs/universal/bin/python" -m json.tool 2>/dev/null || echo "${post_json}"

# --- 3. CDP wake pointer turn + team_dispatch recipe ---
WAKE_BODY="Gate 1 audit posted on thread ${THREAD} turn ${AUDIT_TURN}. Read the structured audit and reply APPROVED or NEEDS_CHANGES with reasoning. No diff inline — audit body is the turn above."

wake_json="$("${AGENT_BUS}" reply \
  --thread "${THREAD}" \
  --to web-anthropic \
  --from-agent cursor \
  --subject "CDP wake — Gate 1 audit turn ${AUDIT_TURN}" \
  --body "${WAKE_BODY}" \
  --after-turn "${AUDIT_TURN}")"

WAKE_TURN="$(printf '%s' "${wake_json}" | "${HOME}/.venvs/universal/bin/python" -c '
import json, sys
data = json.loads(sys.stdin.read())
print(int(data.get("turn_number") or data.get("turn") or 0))
')"

echo "posted wake pointer turn=${WAKE_TURN}"

TEAM_DISPATCH_CMD="team_dispatch(op=generate, model=cdp/opus-5, contract=light-bounded, dispatch_thread_id=${THREAD}, prompt=\"Gate 1 audit on thread ${THREAD} turn ${AUDIT_TURN}. Read bus turn and reply APPROVED or NEEDS_CHANGES.\")"

echo ""
echo "CDP wake (product path — run from code seat with MCP):"
echo "  ${TEAM_DISPATCH_CMD}"
echo ""

if [[ "${INVOKE_CDP_WAKE}" -eq 1 ]]; then
  PROMPT_FILE="${REPO}/tmp/reviews/gate1-audit-wake-${THREAD}-${AUDIT_TURN}.md"
  mkdir -p "$(dirname "${PROMPT_FILE}")"
  cat > "${PROMPT_FILE}" <<EOF
# Gate 1 audit wake

Read agent-bus thread **${THREAD}** turn **${AUDIT_TURN}** (structured audit, no diff inline).

Reply on the same thread with explicit **APPROVED** or **NEEDS_CHANGES** plus reasoning.
EOF
  echo "invoking CDP escape: project-ask (background) prompt=${PROMPT_FILE}"
  "${REPO}/scripts/cortex/claude-ai-sync-jupiter" project-ask \
    --converse --no-uuid --model opus-5 \
    --prompt-file "${PROMPT_FILE}" \
    --out-dir "${REPO}/tmp/reviews/gate1-wake-${THREAD}-${AUDIT_TURN}" &
  echo "project-ask pid=$!"
else
  echo "tip: pass --invoke-cdp-wake to fire claude-ai-sync-jupiter project-ask escape"
fi

# --- 4. Transition pager ---
if [[ "${NO_PAGE}" -eq 0 ]]; then
  if [[ "${PAGER_NOTIFY_ENABLED:-1}" =~ ^(0|false|no|off)$ ]]; then
    echo "pager disabled (PAGER_NOTIFY_ENABLED=${PAGER_NOTIFY_ENABLED:-})"
  elif [[ -S "${EMAIL_BRIDGE_SOCK}" ]]; then
    page_subject="Gate 1 audit in flight — thread ${THREAD}"
    page_body="Claudeburst Gate 1 audit posted to agent-bus thread ${THREAD} turn ${AUDIT_TURN}. Web Claude review is in flight; watcher armed. No action needed unless the page says otherwise."
    page_payload="$(printf '%s' "{\"subject\":\"${page_subject}\",\"body\":\"${page_body}\",\"tag\":\"gate1-audit\"}")"
    pager_resp="$(curl -sS --unix-socket "${EMAIL_BRIDGE_SOCK}" \
      -H 'Content-Type: application/json' \
      -d "${page_payload}" \
      http://localhost/pager/notify || true)"
    echo "transition pager: ${pager_resp}"
  else
    echo "pager skipped: ${EMAIL_BRIDGE_SOCK} not present"
  fi
fi

# --- 5. Arm tmux watcher (watch-bus-consult pattern, detached session) ---
if [[ "${NO_WATCH}" -eq 0 ]]; then
  WATCH_AFTER="${WAKE_TURN}"
  if [[ "${WATCH_AFTER}" -eq 0 ]]; then
    WATCH_AFTER="${AUDIT_TURN}"
  fi
  slug="$(printf '%s' "${WATCH_LABEL}" | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9' '-')"
  slug="${slug#-}"
  slug="${slug%-}"
  SESSION="watch-${slug}"
  LOG="/tmp/${SESSION}.log"
  PYTHON="${HOME}/.venvs/universal/bin/python"
  watch_args=(
    scripts/watch-bus-consult-and-page.py
    --thread "${THREAD}"
    --after-turn "${WATCH_AFTER}"
    --from-agent web-anthropic
    --label "${WATCH_LABEL}"
  )
  quoted_args=""
  for arg in "${watch_args[@]}"; do
    quoted_args+=$(printf '%q ' "$arg")
  done
  inner="cd ${REPO} && ${PYTHON} ${quoted_args}; echo; echo '--- watcher done ---'; read"
  if tmux has-session -t "${SESSION}" 2>/dev/null; then
    tmux kill-session -t "${SESSION}"
  fi
  tmux new-session -d -s "${SESSION}" -n "${WATCH_LABEL}" \
    "bash -lc $(printf '%q' "${inner} | tee -a ${LOG}")"
  echo ""
  echo "watcher armed — attach: tmux attach -t ${SESSION}"
  echo "log: ${LOG}"
fi

echo ""
echo "done — audit turn ${AUDIT_TURN} on thread ${THREAD}"
