#!/usr/bin/env bash
# Preflight: GPU image build + jupiter → io stream transfer.
# Run FROM io (master home) with repo checkout available.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
IMAGE_REF="${IMAGE_REF:-universal-llm-gateway:gpu}"
REMOTE_SSH="${REMOTE_SSH:-jupiter}"
LOCAL_HOST="$(hostname -s 2>/dev/null || hostname)"

section() { echo ""; echo "=== $* ==="; }

fail=0
warn() { echo "WARN: $*"; }
ok() { echo "OK:   $*"; }
bad() { echo "FAIL: $*"; fail=1; }

section "Local ($LOCAL_HOST)"
if command -v nvidia-smi >/dev/null 2>&1; then
  nvidia-smi --query-gpu=name,driver_version --format=csv,noheader || true
else
  warn "nvidia-smi missing on $LOCAL_HOST"
fi

if docker info >/dev/null 2>&1; then
  ok "docker daemon"
  docker info 2>/dev/null | rg -i "Storage Driver|Runtimes" || true
else
  bad "docker not available on $LOCAL_HOST"
fi

df -h / | tail -1
avail_g="$(df -BG / | tail -1 | awk '{print $4}' | tr -d G)"
if [[ "${avail_g:-0}" -lt 25 ]]; then
  warn "root filesystem has ~${avail_g}G free — stream load needs headroom; prune unused images first"
  docker system df 2>/dev/null || true
else
  ok "root free space ~${avail_g}G"
fi

if docker image inspect "$IMAGE_REF" >/dev/null 2>&1; then
  docker image inspect "$IMAGE_REF" --format "local image Id={{.Id}} Size={{.Size}} Created={{.Created}}"
else
  warn "no local $IMAGE_REF"
fi

edge_local="$(docker ps -a --filter "name=^edge-localhost$" --format '{{.Names}} {{.Status}}' 2>/dev/null || true)"
[[ -n "$edge_local" ]] && echo "edge: $edge_local"

section "Remote ($REMOTE_SSH)"
if ! ssh -o BatchMode=yes -o ConnectTimeout=10 "$REMOTE_SSH" 'hostname' >/dev/null 2>&1; then
  bad "ssh $REMOTE_SSH (BatchMode) failed"
else
  ok "ssh $REMOTE_SSH"
  ssh -o BatchMode=yes "$REMOTE_SSH" bash -s <<'EOS'
set -euo pipefail
IMAGE_REF="${IMAGE_REF:-universal-llm-gateway:gpu}"
if command -v nvidia-smi >/dev/null 2>&1; then
  nvidia-smi --query-gpu=name,driver_version --format=csv,noheader
fi
if ! docker info >/dev/null 2>&1; then
  echo "FAIL: docker not available on $(hostname)"
  exit 2
fi
docker info 2>/dev/null | rg -i "Storage Driver|Runtimes" || true
df -h / | tail -1
if docker image inspect "$IMAGE_REF" >/dev/null 2>&1; then
  docker image inspect "$IMAGE_REF" --format "remote image Id={{.Id}} Size={{.Size}} Created={{.Created}}"
else
  echo "WARN: no $IMAGE_REF on $(hostname)"
fi
docker ps -a --filter "name=^edge-jupiter$" --format 'edge: {{.Names}} {{.Status}}' || true
test -d /mnt/torus/projects/universal-llm-gateway && echo "OK:   repo /mnt/torus/projects/universal-llm-gateway" || echo "WARN: repo path missing"
EOS
fi

section "Pin defaults (local build-gpu.sh)"
rg 'LLAMA_SERVER_VERSION="\$\{|VLLM_VERSION="\$\{' "$REPO_ROOT/docker/scripts/build/build-gpu.sh" | head -4 || true

section "Transfer readiness"
echo "Recommended path: BUILD on $REMOTE_SSH → docker save | ssh io docker load (no giant tar on io disk)."
echo "Image names must match: $IMAGE_REF (gpu-edge.yml pull_policy: never)."

if [[ "$fail" -ne 0 ]]; then
  echo ""
  echo "Preflight FAILED — fix items above before build/transfer."
  exit 1
fi
echo ""
echo "Preflight passed (warnings may still apply)."