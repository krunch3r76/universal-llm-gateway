#!/usr/bin/env bash
# Build MCP server image with optional cache control.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
HELPER="${SCRIPT_DIR}/build-service-image.sh"
BUILD_CONTEXT_PARENT="${PROJECT_ROOT}/tmp/build-contexts"

EXTRA_ARGS=()

while [[ $# -gt 0 ]]; do
    case $1 in
        --no-cache)
            EXTRA_ARGS+=("--no-cache")
            shift
            ;;
        --refresh-source)
            EXTRA_ARGS+=(--build-arg "SOURCE_VERSION=$(date +%s)")
            shift
            ;;
        -h|--help)
            echo "Usage: $0 [--no-cache] [--refresh-source]"
            echo "  --no-cache        Force full rebuild without cache (also refreshes base images)"
            echo "  --refresh-source  Bust source layer cache (re-copy app code)"
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            echo "Usage: $0 [--no-cache] [--refresh-source]" >&2
            exit 1
            ;;
    esac
done

cd "$PROJECT_ROOT"

mkdir -p "${BUILD_CONTEXT_PARENT}"
BUILD_CONTEXT="$(mktemp -d "${BUILD_CONTEXT_PARENT}/mcp.XXXXXX")"
cleanup() {
    rm -rf "${BUILD_CONTEXT}"
}
trap cleanup EXIT

echo "📦 Syncing MCP build context from working tree (.gitignore-aware)..."
rsync -a \
    --delete \
    --exclude=".git" \
    --filter=':- .gitignore' \
    "${PROJECT_ROOT}/" "${BUILD_CONTEXT}/"

bash "${HELPER}" \
    --builder "ulg-mcp" \
    --image "universal-mcp-server:local" \
    --dockerfile "${BUILD_CONTEXT}/services/mcp-server/Dockerfile" \
    --context "${BUILD_CONTEXT}" \
    "${EXTRA_ARGS[@]}"
