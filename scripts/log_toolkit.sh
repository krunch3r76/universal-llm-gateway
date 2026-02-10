#!/usr/bin/env bash
# Universal Logging Toolkit
# Provides multiple ways to view/filter NDJSON logs
#
# Usage:
#   ./scripts/log_toolkit.sh [OPTIONS] logfile
#
# Options:
#   --tail          Live tail with colors (default)
#   --level LEVEL   Filter by log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
#   --jq            Use jq for output (scriptable JSON)
#   --lnav          Open in lnav for interactive queries
#   --raw           Raw JSON output (no formatting)
#
# Examples:
#   ./scripts/log_toolkit.sh --tail /tmp/logs/gateway.log
#   ./scripts/log_toolkit.sh --level ERROR /tmp/logs/gateway.log
#   ./scripts/log_toolkit.sh --jq /tmp/logs/gateway.log > filtered.json

set -euo pipefail

usage() {
    cat << 'EOF'
Universal Logging Toolkit — view and filter NDJSON logs

Usage: log_toolkit.sh [OPTIONS] logfile

Options:
  --tail, -f       Live tail with colors (default for TTY)
  --level LEVEL    Filter by log level
  --jq             Use jq for output (scriptable)
  --lnav           Open in lnav (interactive SQL queries)
  --raw            Raw JSON, no formatting
  -h, --help       Show this help

Examples:
  log_toolkit.sh --tail /tmp/logs/gateway.log
  log_toolkit.sh --level ERROR --tail /tmp/logs/gateway.log
  log_toolkit.sh --jq /tmp/logs/gateway.log | jq '.level'
EOF
}

# Defaults
TAIL=false
LEVEL=""
MODE="python"  # python, jq, lnav, raw
LOGFILE=""

# Parse arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        --tail|-f)
            TAIL=true
            shift
            ;;
        --level)
            LEVEL="${2:-}"
            shift 2
            ;;
        --jq)
            MODE="jq"
            shift
            ;;
        --lnav)
            MODE="lnav"
            shift
            ;;
        --raw)
            MODE="raw"
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        -*)
            echo "Unknown option: $1" >&2
            usage >&2
            exit 1
            ;;
        *)
            LOGFILE="$1"
            shift
            ;;
    esac
done

if [[ -z "$LOGFILE" ]]; then
    echo "Error: logfile required" >&2
    usage >&2
    exit 1
fi

if [[ ! -f "$LOGFILE" ]]; then
    echo "Error: $LOGFILE does not exist" >&2
    exit 1
fi

# Mode handlers
case "$MODE" in
    python)
        SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
        ARGS=("$LOGFILE")
        [[ "$TAIL" == "true" ]] && ARGS=("--tail" "${ARGS[@]}")
        [[ -n "$LEVEL" ]] && ARGS=("--level" "$LEVEL" "${ARGS[@]}")
        exec python3 "$SCRIPT_DIR/log_viewer.py" "${ARGS[@]}"
        ;;
    jq)
        if [[ -n "$LEVEL" ]]; then
            FILTER="select(.level==\"$LEVEL\")"
        else
            FILTER="."
        fi
        if [[ "$TAIL" == "true" ]]; then
            exec tail -f "$LOGFILE" | jq -C "$FILTER"
        else
            exec jq -C "$FILTER" "$LOGFILE"
        fi
        ;;
    lnav)
        if ! command -v lnav &>/dev/null; then
            echo "Error: lnav not installed. Install with: sudo apt install lnav" >&2
            exit 1
        fi
        exec lnav "$LOGFILE"
        ;;
    raw)
        if [[ "$TAIL" == "true" ]]; then
            exec tail -f "$LOGFILE"
        else
            exec cat "$LOGFILE"
        fi
        ;;
esac
