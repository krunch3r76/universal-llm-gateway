#!/usr/bin/env bash
# Build universal-llm-gateway:gpu on Jupiter and stream-load it on io (no on-disk tar).
#
# Run subcommands FROM io (master), cwd = repo root:
#   ./docker/scripts/build/rollout-gpu-image-transferable.sh preflight
#   ./docker/scripts/build/rollout-gpu-image-transferable.sh build-on-jupiter
#   ./docker/scripts/build/rollout-gpu-image-transferable.sh sync-image
#   ./docker/scripts/build/rollout-gpu-image-transferable.sh restart-jupiter-edge
#   ./docker/scripts/build/rollout-gpu-image-transferable.sh restart-io-edge
#   ./docker/scripts/build/rollout-gpu-image-transferable.sh all
#
# Optional env:
#   REMOTE_SSH=jupiter          SSH config host for Jupiter relay
#   IMAGE_REF=universal-llm-gateway:gpu
#   VLLM_FROM_SOURCE=false      Recommended for v0.34.0 pin until patch workflows exist
#   NO_CACHE=1                  Pass --no-cache to build-gpu.sh
#   SKIP_RSYNC=1                Skip repo rsync to Jupiter before build
#
# Ctrl-C during build-on-jupiter stops buildx on the remote host (not orphaned).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=docker/scripts/build/gpu-build-signal-cleanup.sh
source "${SCRIPT_DIR}/gpu-build-signal-cleanup.sh"
REMOTE_SSH="${REMOTE_SSH:-jupiter}"
IMAGE_REF="${IMAGE_REF:-universal-llm-gateway:gpu}"
REMOTE_REPO="${REMOTE_REPO:-/mnt/torus/projects/universal-llm-gateway}"
BUILD_EXTRA_FLAGS=()

# Safer default for pinned v0.34.0 wheel path unless operator overrides.
export VLLM_FROM_SOURCE="${VLLM_FROM_SOURCE:-false}"

if [[ "${NO_CACHE:-0}" == "1" ]]; then
  BUILD_EXTRA_FLAGS+=(--no-cache)
fi
if [[ -n "${GPU_ARCH:-}" && "${GPU_ARCH}" != "multi" ]]; then
  BUILD_EXTRA_FLAGS+=(--gpu-arch="${GPU_ARCH}")
fi

usage() {
  sed -n '4,12p' "$0"
  exit 1
}

cmd_preflight() {
  exec "$SCRIPT_DIR/preflight-gpu-image-transfer.sh"
}

cmd_rsync_jupiter() {
  if [[ "${SKIP_RSYNC:-0}" == "1" ]]; then
    echo "SKIP_RSYNC=1 — using existing tree on $REMOTE_SSH"
    return 0
  fi
  local dest
  dest="$(ssh -o BatchMode=yes "$REMOTE_SSH" 'printf %s "$USER"')"
  echo "Rsync repo → ${REMOTE_SSH}:~/universal-llm-gateway/ (shared torus path may already match)"
  rsync -az \
    --exclude=.git \
    --filter=':- .gitignore' \
    "$REPO_ROOT/" \
    "${REMOTE_SSH}:~/universal-llm-gateway/"
  ssh -o BatchMode=yes "$REMOTE_SSH" "test -d '$REMOTE_REPO' || ln -sfn \"\$HOME/universal-llm-gateway\" '$REMOTE_REPO' 2>/dev/null || true"
}

cmd_build_on_jupiter() {
  cmd_rsync_jupiter
  echo "Building $IMAGE_REF on $REMOTE_SSH (VLLM_FROM_SOURCE=$VLLM_FROM_SOURCE, TORCH_NIGHTLY_DATE=${TORCH_NIGHTLY_DATE:-latest})…"
  echo "(Ctrl-C stops the remote build on $REMOTE_SSH.)"
  ulg_arm_rollout_ssh_trap "$REMOTE_SSH"
  # Expand GPU_ARCH / VLLM_* here on io so the remote session inherits intent (not remote default multi).
  if ! ssh -o BatchMode=yes -t "$REMOTE_SSH" bash -s <<EOS
set -euo pipefail
cd "$REMOTE_REPO"
# shellcheck source=docker/scripts/build/gpu-build-signal-cleanup.sh
source docker/scripts/build/gpu-build-signal-cleanup.sh
ulg_arm_local_gpu_build_trap
export VLLM_FROM_SOURCE="${VLLM_FROM_SOURCE}"
export GPU_ARCH="${GPU_ARCH:-multi}"
export VLLM_BUILD_ARGS="${VLLM_BUILD_ARGS:---portable}"
export VLLM_MAX_JOBS="${VLLM_MAX_JOBS:-8}"
export VLLM_VERSION="${VLLM_VERSION:-}"
export TORCH_NIGHTLY_DATE="${TORCH_NIGHTLY_DATE:-latest}"
./docker/scripts/build/build-gpu.sh ${BUILD_EXTRA_FLAGS[*]:-}
ulg_disarm_gpu_build_trap
docker image inspect "$IMAGE_REF" --format 'built Id={{.Id}} Created={{.Created}}'
EOS
  then
    ulg_disarm_gpu_build_trap
    return 1
  fi
  ulg_disarm_gpu_build_trap
}

cmd_sync_image() {
  if docker image inspect "$IMAGE_REF" >/dev/null 2>&1; then
    local backup="gpu-prev-$(date +%Y%m%d%H%M)"
    echo "Tagging current io image → universal-llm-gateway:${backup}"
    docker tag "$IMAGE_REF" "universal-llm-gateway:${backup}"
  fi
  echo "Streaming $IMAGE_REF from $REMOTE_SSH → local docker load (this takes a while)…"
  ssh -o BatchMode=yes "$REMOTE_SSH" "docker save '$IMAGE_REF'" | docker load
  local rid lid
  rid="$(ssh -o BatchMode=yes "$REMOTE_SSH" docker image inspect "$IMAGE_REF" --format '{{.Id}}')"
  lid="$(docker image inspect "$IMAGE_REF" --format '{{.Id}}')"
  if [[ "$rid" != "$lid" ]]; then
    echo "FAIL: image Id mismatch after transfer"
    echo "  remote: $rid"
    echo "  local:  $lid"
    exit 1
  fi
  echo "OK: image Id match after stream load"
  docker image inspect "$IMAGE_REF" --format 'local Id={{.Id}} Size={{.Size}}'
}

_restart_edge_on() {
  local where="$1" # jupiter | io
  if [[ "$where" == "jupiter" ]]; then
    ssh -o BatchMode=yes -t "$REMOTE_SSH" bash -s <<'EOS'
set -euo pipefail
cd /mnt/torus/projects/universal-llm-gateway
set -a
source "$HOME/.gateway/nodes/jupiter.env"
set +a
export COMPOSE_PROJECT_NAME="edge-${NODE_ID}"
docker compose -f docker/compose/gpu-edge.yml up -d --force-recreate
docker ps --filter "name=^edge-jupiter$" --format '{{.Names}} {{.Status}}'
EOS
  else
    cd "$REPO_ROOT"
    set -a
    # shellcheck source=/dev/null
    source "$HOME/.gateway/nodes/localhost.env"
    set +a
    export COMPOSE_PROJECT_NAME="edge-${NODE_ID}"
    docker compose -f docker/compose/gpu-edge.yml up -d --force-recreate
    docker ps --filter "name=^edge-localhost$" --format '{{.Names}} {{.Status}}'
  fi
}

cmd_restart_jupiter_edge() { _restart_edge_on jupiter; }
cmd_restart_io_edge() { _restart_edge_on io; }

cmd_all() {
  cmd_preflight
  cmd_build_on_jupiter
  cmd_sync_image
  cmd_restart_jupiter_edge
  cmd_restart_io_edge
  echo ""
  echo "Done. From io, smoke: curl -s http://localhost:9999/v1/models?include_sources=true | head"
}

case "${1:-}" in
  preflight) cmd_preflight ;;
  rsync-jupiter) cmd_rsync_jupiter ;;
  build-on-jupiter) cmd_build_on_jupiter ;;
  sync-image) cmd_sync_image ;;
  restart-jupiter-edge) cmd_restart_jupiter_edge ;;
  restart-io-edge) cmd_restart_io_edge ;;
  all) cmd_all ;;
  -h|--help|"") usage ;;
  *) echo "Unknown subcommand: $1" >&2; usage ;;
esac