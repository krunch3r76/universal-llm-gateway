#!/usr/bin/env bash
# Cwd-safe launcher for resume_fence_hook.py (manual smoke / legacy).
# Installed plugin + workspace hooks.json use python3 scripts/cursor/resume_fence_hook.py
# directly — plugin cwd is ~/.cursor/plugins/local/ulg-ecosystem (not the repo).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$HERE/resume_fence_hook.py" "$@"
