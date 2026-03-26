#!/usr/bin/env bash
# Compatibility wrapper for the canonical GPU build script.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ "${1:-}" == "--skip-cache-clear" ]]; then
    echo "Ignoring obsolete --skip-cache-clear flag."
    echo "Use ./scripts/build-cache.sh prune gateway for explicit scoped cleanup."
    shift
fi

exec bash "${SCRIPT_DIR}/build-gpu.sh" "$@"
