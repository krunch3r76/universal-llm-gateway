#!/usr/bin/env bash
# Build one compose-managed service image via a dedicated buildx builder.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"

BUILDER=""
IMAGE=""
DOCKERFILE=""
CONTEXT="${PROJECT_ROOT}"
NO_CACHE=false
PULL=false
BUILD_ARGS=()
EFFECTIVE_BUILDER=""
TEMP_BUILDER=false

cleanup() {
  if [[ "${TEMP_BUILDER}" == "true" && -n "${EFFECTIVE_BUILDER}" ]]; then
    echo "Removing ephemeral no-cache builder: ${EFFECTIVE_BUILDER}"
    docker buildx rm "${EFFECTIVE_BUILDER}" >/dev/null 2>&1 || true
  fi
}

trap cleanup EXIT

usage() {
  cat <<'EOF'
Usage: ./docker/scripts/build/build-service-image.sh \
  --builder <name> \
  --image <repo:tag> \
  --dockerfile <path> \
  [--context <path>] \
  [--no-cache] \
  [--pull] \
  [--build-arg KEY=VALUE ...]

Notes:
  - `--no-cache` also implies `--pull` in this workspace.
  - Cache-preserving builds reuse the named builder you pass via `--builder`.
  - `--no-cache` builds create a temporary per-run builder and remove it on exit.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --builder)
      BUILDER="$2"
      shift 2
      ;;
    --image)
      IMAGE="$2"
      shift 2
      ;;
    --dockerfile)
      DOCKERFILE="$2"
      shift 2
      ;;
    --context)
      CONTEXT="$2"
      shift 2
      ;;
    --no-cache)
      NO_CACHE=true
      PULL=true
      shift
      ;;
    --pull)
      PULL=true
      shift
      ;;
    --build-arg)
      BUILD_ARGS+=("--build-arg" "$2")
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

if [[ -z "${BUILDER}" || -z "${IMAGE}" || -z "${DOCKERFILE}" ]]; then
  echo "Missing required options." >&2
  usage >&2
  exit 1
fi

cd "${PROJECT_ROOT}"

if [[ "${NO_CACHE}" == "true" ]]; then
  EFFECTIVE_BUILDER="${BUILDER}-rebuild-$(date +%s)-$$"
  TEMP_BUILDER=true
  echo "Creating ephemeral no-cache builder: ${EFFECTIVE_BUILDER}"
  docker buildx create --name "${EFFECTIVE_BUILDER}" --driver docker-container >/dev/null
  docker buildx inspect --bootstrap "${EFFECTIVE_BUILDER}" >/dev/null
else
  EFFECTIVE_BUILDER="${BUILDER}"
  if ! docker buildx inspect "${EFFECTIVE_BUILDER}" >/dev/null 2>&1; then
    echo "Creating buildx builder: ${EFFECTIVE_BUILDER}"
    docker buildx create --name "${EFFECTIVE_BUILDER}" --driver docker-container >/dev/null
  fi

  if ! docker buildx inspect --bootstrap "${EFFECTIVE_BUILDER}" >/dev/null 2>&1; then
    echo "Recreating stale buildx builder: ${EFFECTIVE_BUILDER}"
    docker buildx rm "${EFFECTIVE_BUILDER}" >/dev/null 2>&1 || true
    docker buildx create --name "${EFFECTIVE_BUILDER}" --driver docker-container >/dev/null
    docker buildx inspect --bootstrap "${EFFECTIVE_BUILDER}" >/dev/null
  fi
fi

CMD=(
  docker buildx build
  --builder "${EFFECTIVE_BUILDER}"
  --load
  --progress=plain
  --tag "${IMAGE}"
  --file "${DOCKERFILE}"
)

if [[ "${NO_CACHE}" == "true" ]]; then
  CMD+=(--no-cache)
fi

if [[ "${PULL}" == "true" ]]; then
  CMD+=(--pull)
fi

CMD+=("${BUILD_ARGS[@]}")
CMD+=("${CONTEXT}")

echo "Building ${IMAGE} with builder ${EFFECTIVE_BUILDER}..."
"${CMD[@]}"
