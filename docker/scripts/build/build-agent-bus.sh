#!/usr/bin/env bash
# Build agent-bus via a dedicated buildx builder.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
HELPER="${SCRIPT_DIR}/build-service-image.sh"

EXTRA_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-cache)
      EXTRA_ARGS+=("--no-cache")
      shift
      ;;
    -h|--help)
      echo "Usage: $0 [--no-cache]"
      echo "  --no-cache  Force full rebuild without cache (also refreshes base images)"
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
  esac
done

cd "${PROJECT_ROOT}"
bash "${HELPER}" \
  --builder "ulg-agent-bus" \
  --image "universal-agent-bus:local" \
  --dockerfile "services/agent-bus/Dockerfile" \
  "${EXTRA_ARGS[@]}"
