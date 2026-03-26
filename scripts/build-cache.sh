#!/usr/bin/env bash
# Inspect or prune dedicated buildx caches for this workspace.

set -euo pipefail

declare -A BUILDERS=(
  [gateway]="ulg-gateway"
  [mcp]="ulg-mcp"
  [cortex-api]="ulg-cortex-api"
  [agent-bus]="ulg-agent-bus"
  [event-service]="ulg-event-service"
)

usage() {
  cat <<'EOF'
Usage:
  ./scripts/build-cache.sh list
  ./scripts/build-cache.sh size <target>
  ./scripts/build-cache.sh prune <target>

Targets:
  gateway
  mcp
  cortex-api
  agent-bus
  event-service
  all
EOF
}

resolve_builder() {
  local target="$1"
  if [[ -z "${BUILDERS[$target]:-}" ]]; then
    echo "Unknown target: ${target}" >&2
    exit 1
  fi
  printf '%s\n' "${BUILDERS[$target]}"
}

builder_exists() {
  local builder="$1"
  docker buildx inspect "${builder}" >/dev/null 2>&1
}

size_one() {
  local target="$1"
  local builder
  builder="$(resolve_builder "${target}")"
  if ! builder_exists "${builder}"; then
    exit 0
  fi
  docker buildx du --builder "${builder}" --verbose \
    | awk -F': ' '/^Total:/ { print $2; found=1 } END { if (!found) exit 1 }'
}

prune_one() {
  local target="$1"
  local builder
  builder="$(resolve_builder "${target}")"
  if ! builder_exists "${builder}"; then
    echo "Builder ${builder} does not exist yet."
    return 0
  fi
  echo "Pruning build cache for ${target} (${builder})..."
  docker buildx prune --builder "${builder}" -af
}

if [[ $# -lt 1 ]]; then
  usage >&2
  exit 1
fi

COMMAND="$1"
TARGET="${2:-}"

case "${COMMAND}" in
  list)
    for target in gateway mcp cortex-api agent-bus event-service; do
      printf '%s\t%s\n' "${target}" "${BUILDERS[$target]}"
    done
    ;;
  size)
    if [[ -z "${TARGET}" ]]; then
      usage >&2
      exit 1
    fi
    if [[ "${TARGET}" == "all" ]]; then
      echo "size all is not supported; query one target at a time." >&2
      exit 1
    fi
    size_one "${TARGET}"
    ;;
  prune)
    if [[ -z "${TARGET}" ]]; then
      usage >&2
      exit 1
    fi
    if [[ "${TARGET}" == "all" ]]; then
      for target in gateway mcp cortex-api agent-bus event-service; do
        prune_one "${target}"
      done
    else
      prune_one "${TARGET}"
    fi
    ;;
  -h|--help)
    usage
    ;;
  *)
    echo "Unknown command: ${COMMAND}" >&2
    usage >&2
    exit 1
    ;;
esac
