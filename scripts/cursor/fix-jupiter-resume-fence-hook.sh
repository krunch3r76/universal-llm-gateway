#!/usr/bin/env bash
# Install resume_fence_hook.py into the ulg-ecosystem plugin on Jupiter and/or
# clear stale local resume-fence markers. Cursor hooks run on the client host
# (Jupiter), not on the workspace NFS host (io).
#
# Usage (from io with `ssh jupiter`, or on Jupiter with --local):
#   scripts/cursor/fix-jupiter-resume-fence-hook.sh
#   scripts/cursor/fix-jupiter-resume-fence-hook.sh --local --clear-markers-only
#   scripts/cursor/fix-jupiter-resume-fence-hook.sh --dry-run
#   scripts/cursor/fix-jupiter-resume-fence-hook.sh --hook-only
#
# Env:
#   JUPITER_SSH_TARGET  SSH target (default: jupiter). Use `local` or pass --local on Jupiter.
#   ULG_ROOT            Repo path on Jupiter (default: /mnt/torus/projects/universal-llm-gateway)

set -euo pipefail

SSH_TARGET="${JUPITER_SSH_TARGET:-${JUPITER_SSH_HOST:-jupiter}}"
ULG_ROOT="${ULG_ROOT:-/mnt/torus/projects/universal-llm-gateway}"
DRY_RUN=0
DO_HOOK=1
DO_MARKERS=1
RUN_LOCAL=0

usage() {
  sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
  exit "${1:-0}"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --local) RUN_LOCAL=1 ;;
    --dry-run) DRY_RUN=1 ;;
    --hook-only) DO_MARKERS=0 ;;
    --clear-markers-only) DO_HOOK=0 ;;
    -h|--help) usage 0 ;;
    *)
      echo "ERROR: unknown arg: $1" >&2
      usage 1
      ;;
  esac
  shift
done

if [[ "$DO_HOOK" -eq 0 && "$DO_MARKERS" -eq 0 ]]; then
  echo "ERROR: nothing to do (use default or --hook-only / --clear-markers-only)" >&2
  exit 1
fi

if [[ "$RUN_LOCAL" -eq 1 || "$SSH_TARGET" == "local" ]]; then
  RUN_LOCAL=1
  SSH_TARGET="local ($(hostname))"
fi

echo "=== fix jupiter resume-fence hook ==="
echo "ssh_target=$SSH_TARGET"
echo "run_local=$RUN_LOCAL"
echo "ulg_root=$ULG_ROOT"
echo "hook=$([[ "$DO_HOOK" -eq 1 ]] && echo yes || echo no)"
echo "clear_markers=$([[ "$DO_MARKERS" -eq 1 ]] && echo yes || echo no)"
echo "dry_run=$DRY_RUN"

REMOTE_SCRIPT=$(cat <<'REMOTE'
set -euo pipefail
ULG_ROOT="$1"
DO_HOOK="$2"
DO_MARKERS="$3"
DRY_RUN="$4"

PLUGIN_ROOT="${HOME}/.cursor/plugins/local/ulg-ecosystem"
PLUGIN_HOOK_DIR="${PLUGIN_ROOT}/scripts/cursor"
PLUGIN_HOOKS_JSON="${PLUGIN_ROOT}/hooks/hooks.json"
HOOK_SRC="${ULG_ROOT}/scripts/cursor/resume_fence_hook.py"
HOOKS_JSON_SRC="${ULG_ROOT}/cursor-plugins/ulg-ecosystem/hooks/hooks.json"
HOOK_DST="${PLUGIN_HOOK_DIR}/resume_fence_hook.py"
MARKER_DIR="${HOME}/.agent-bus/resume-fence"

run() {
  if [[ "$DRY_RUN" == "1" ]]; then
    echo "[dry-run] $*"
  else
    eval "$@"
  fi
}

if [[ "$DO_HOOK" == "1" ]]; then
  if [[ ! -f "$HOOK_SRC" ]]; then
    echo "ERROR: hook source missing on Jupiter: $HOOK_SRC" >&2
    exit 1
  fi
  run "mkdir -p $(printf '%q' "$PLUGIN_HOOK_DIR")"
  run "mkdir -p $(printf '%q' "$(dirname "$PLUGIN_HOOKS_JSON")")"
  # NFS home (.cursor) often rejects cp -a permission preservation.
  run "cp -f $(printf '%q' "$HOOK_SRC") $(printf '%q' "$HOOK_DST")"
  run "chmod 755 $(printf '%q' "$HOOK_DST")"
  if [[ -f "$HOOKS_JSON_SRC" ]]; then
    run "cp -f $(printf '%q' "$HOOKS_JSON_SRC") $(printf '%q' "$PLUGIN_HOOKS_JSON")"
  fi
  if [[ "$DRY_RUN" != "1" ]]; then
    test -f "$HOOK_DST" || { echo "ERROR: install failed: $HOOK_DST" >&2; exit 1; }
    echo "hook_installed=$HOOK_DST"
    if [[ -f "$PLUGIN_HOOKS_JSON" ]]; then
      echo "plugin_hooks_json=$PLUGIN_HOOKS_JSON"
      grep -E 'resume_fence|run-resume-fence' "$PLUGIN_HOOKS_JSON" || true
    fi
    echo "hook_sha=$(sha256sum "$HOOK_DST" | awk '{print $1}')"
    if command -v python3 >/dev/null 2>&1; then
      smoke=$(printf '%s' '{"conversation_id":"smoke","tool_name":"Mcp","tool_input":{"tool":"continuity","arguments":{"op":"resume","thread":"10223"}}}' \
        | python3 "$HOOK_DST" --event preToolUse)
      echo "hook_smoke=$smoke"
    fi
  else
    echo "[dry-run] would install $HOOK_SRC -> $HOOK_DST"
  fi
fi

if [[ "$DO_MARKERS" == "1" ]]; then
  if [[ -d "$MARKER_DIR" ]]; then
    count=$(find "$MARKER_DIR" -maxdepth 1 -name '*.json' 2>/dev/null | wc -l | tr -d ' ')
    if [[ "$count" -gt 0 ]]; then
      run "rm -f $(printf '%q' "$MARKER_DIR")/*.json"
      echo "markers_cleared=$count"
    else
      echo "markers_cleared=0 (none)"
    fi
  else
    echo "markers_cleared=0 (dir missing)"
  fi
  deny_log="${MARKER_DIR}/denied-fallback.jsonl"
  if [[ -f "$deny_log" ]]; then
    echo "denied_fallback_tail:"
    tail -3 "$deny_log" || true
  fi
fi

echo "done host=$(hostname) user=$(whoami)"
REMOTE
)

run_remote() {
  local dry_flag="$1"
  if [[ "$RUN_LOCAL" -eq 1 ]]; then
    bash -s -- "$ULG_ROOT" "$DO_HOOK" "$DO_MARKERS" "$dry_flag" <<<"$REMOTE_SCRIPT"
  else
    ssh -o BatchMode=yes "$SSH_TARGET" "bash -s" -- "$ULG_ROOT" "$DO_HOOK" "$DO_MARKERS" "$dry_flag" <<<"$REMOTE_SCRIPT"
  fi
}

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "--- plan ---"
  run_remote 1
  echo "---"
  echo "Re-run without --dry-run to apply"
  echo "Then on Jupiter Cursor: fresh tab -> resume 10223 (hook script is per-invocation; no reload needed)"
  exit 0
fi

run_remote 0

echo
echo "DONE on $SSH_TARGET"
echo "Next: fresh chat on Jupiter -> resume 10223 (hook reads script from disk each call)"
