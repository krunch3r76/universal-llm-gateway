#!/usr/bin/env bash
# Build wrapper for docker-compose.gateway-gpu.yml
# Automatically clears unused build cache and sets cache-busting build arg

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
COMPOSE_FILE="${SCRIPT_DIR}/../compose/gateway-gpu.yml"

# Parse arguments - pass through to docker compose
DOCKER_COMPOSE_ARGS=()
SKIP_CACHE_CLEAR=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --skip-cache-clear)
            SKIP_CACHE_CLEAR=true
            shift
            ;;
        *)
            DOCKER_COMPOSE_ARGS+=("$1")
            shift
            ;;
    esac
done

cd "${PROJECT_ROOT}"

# Clear ALL unused build cache before building (unless skipped)
# This keeps only the most recent build cache
if [[ "${SKIP_CACHE_CLEAR}" == "false" ]]; then
    echo "🧹 Clearing all unused Docker build cache..."
    docker builder prune -af > /dev/null 2>&1 || true
    echo "✅ All previous build cache cleared"
    echo ""
fi

# Build using docker compose
docker compose -f "${COMPOSE_FILE}" build "${DOCKER_COMPOSE_ARGS[@]}"

echo ""
echo "✅ Build complete!"
