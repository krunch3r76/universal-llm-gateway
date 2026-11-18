#!/usr/bin/env bash
# Shared INT/TERM cleanup for build-gpu.sh and remote rollout over SSH.
# Sourced only — do not execute directly.
set -euo pipefail

ulg_stop_gpu_build_processes() {
  pkill -P "$$" 2>/dev/null || true
  pkill -f "docker/scripts/build/build-gpu.sh" 2>/dev/null || true
  pkill -f "docker buildx build.*Dockerfile.gpu" 2>/dev/null || true
  pkill -f "docker/build_llama_server.sh|/build/build_llama_server.sh" 2>/dev/null || true
}

ulg_stop_remote_gpu_build() {
  local host="${1:?ssh host}"
  ssh -o BatchMode=yes -o ConnectTimeout=10 "$host" bash -s <<'EOS'
set +e
pkill -f "docker/scripts/build/build-gpu.sh"
pkill -f "docker buildx build.*Dockerfile.gpu"
pkill -f "build/build_llama_server.sh"
EOS
}

_ulg_local_build_trap() {
  echo "" >&2
  echo "Interrupted — stopping local GPU build (buildx / build-gpu.sh)…" >&2
  ulg_stop_gpu_build_processes
  exit 130
}

_ulg_rollout_ssh_trap() {
  local host="${ULG_ROLLOUT_REMOTE_SSH:-jupiter}"
  echo "" >&2
  echo "Interrupted — stopping GPU build on ${host}…" >&2
  ulg_stop_remote_gpu_build "$host"
  trap - INT TERM
  exit 130
}

ulg_arm_local_gpu_build_trap() {
  trap _ulg_local_build_trap INT TERM
}

ulg_arm_rollout_ssh_trap() {
  local host="${1:-jupiter}"
  ULG_ROLLOUT_REMOTE_SSH="$host"
  export ULG_ROLLOUT_REMOTE_SSH
  trap _ulg_rollout_ssh_trap INT TERM
}

ulg_disarm_gpu_build_trap() {
  trap - INT TERM
}