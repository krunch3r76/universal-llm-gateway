#!/usr/bin/env bash
# Build MCP server image with optional cache control.
#
# Usage:
#   ./docker/scripts/build/build-mcp.sh              # cached build
#   ./docker/scripts/build/build-mcp.sh --no-cache   # full rebuild
#   ./docker/scripts/build/build-mcp.sh --refresh-source  # bust source layer only
#
# SOURCE_VERSION: set to force source-layer cache bust, e.g. SOURCE_VERSION=$(date +%s)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"

COMPOSE_FILE="${PROJECT_ROOT}/docker/compose/mcp-server.yml"
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
            echo "  --no-cache        Force full rebuild without cache"
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
docker compose -f "$COMPOSE_FILE" build "${EXTRA_ARGS[@]}"
