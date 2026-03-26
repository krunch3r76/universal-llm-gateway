#!/usr/bin/env bash
# Read-only audit for workspace Docker build cache footprint.

set -euo pipefail

declare -A BUILDERS=(
  [gateway]="ulg-gateway"
  [mcp]="ulg-mcp"
  [cortex-api]="ulg-cortex-api"
  [agent-bus]="ulg-agent-bus"
  [event-service]="ulg-event-service"
)

declare -A IMAGES=(
  [gateway]="universal-llm-gateway:gpu"
  [mcp]="universal-mcp-server:local"
  [cortex-api]="universal-cortex-api:local"
  [agent-bus]="universal-agent-bus:local"
  [event-service]="universal-event-service:local"
)

TARGETS=(gateway mcp cortex-api agent-bus event-service)

parse_size_to_bytes() {
  python3 - "$1" <<'PY'
import re
import sys

raw = sys.argv[1].strip()
if not raw:
    print(0)
    raise SystemExit

match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)\s*([kmgtp]?)(i?)b?", raw, re.IGNORECASE)
if not match:
    raise SystemExit(f"unrecognized size: {raw}")

value = float(match.group(1))
unit = match.group(2).lower()
binary = bool(match.group(3))
power = {"": 0, "k": 1, "m": 2, "g": 3, "t": 4, "p": 5}[unit]
base = 1024 if binary else 1000
print(int(value * (base ** power)))
PY
}

format_bytes() {
  python3 - "$1" <<'PY'
import sys

size = int(sys.argv[1])
units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"]
value = float(size)
for unit in units:
    if value < 1024 or unit == units[-1]:
        if unit == "B":
            print(f"{int(value)}{unit}")
        else:
            print(f"{value:.2f}{unit}")
        break
    value /= 1024
PY
}

builder_exists() {
  local builder="$1"
  docker buildx inspect "${builder}" >/dev/null 2>&1
}

builder_field_bytes() {
  local builder="$1"
  local field="$2"
  local raw

  raw="$(
    docker buildx du --builder "${builder}" --verbose \
      | awk -F': *' -v field="${field}" '$1 == field { value = $2 } END { print value }'
  )"
  if [[ -z "${raw}" ]]; then
    echo 0
    return 0
  fi
  parse_size_to_bytes "${raw}"
}

image_size_bytes() {
  local image="$1"
  docker image inspect --format '{{.Size}}' "${image}" 2>/dev/null || true
}

dangling_image_count() {
  docker image ls --filter dangling=true -q | awk 'NF {count += 1} END {print count + 0}'
}

dangling_image_bytes() {
  local ids
  ids="$(docker image ls --filter dangling=true -q)"
  if [[ -z "${ids}" ]]; then
    echo 0
    return 0
  fi
  docker image inspect --format '{{.Size}}' ${ids} | awk '{sum += $1} END {print sum + 0}'
}

docker_root_dir() {
  docker info --format '{{.DockerRootDir}}' 2>/dev/null || true
}

host_disk_line() {
  local path="$1"
  df -hP "${path}" | awk 'NR == 2 { printf "%s %s used, %s avail, %s full", $6, $3, $4, $5 }'
}

print_header() {
  echo "Build Cache Audit"
  echo "Generated: $(date -u '+%Y-%m-%d %H:%M:%SZ')"
  echo
}

print_builder_section() {
  local total_all=0
  local max_target=""
  local max_bytes=0
  local target builder total reclaimable

  echo "Builder cache"
  printf '%-14s %-22s %-10s %-12s\n' "target" "builder" "total" "reclaimable"

  for target in "${TARGETS[@]}"; do
    builder="${BUILDERS[$target]}"
    if builder_exists "${builder}"; then
      total="$(builder_field_bytes "${builder}" "Total")"
      reclaimable="$(builder_field_bytes "${builder}" "Reclaimable")"
      printf '%-14s %-22s %-10s %-12s\n' \
        "${target}" \
        "${builder}" \
        "$(format_bytes "${total}")" \
        "$(format_bytes "${reclaimable}")"
      total_all=$((total_all + total))
      if (( total > max_bytes )); then
        max_bytes="${total}"
        max_target="${target}"
      fi
    else
      printf '%-14s %-22s %-10s %-12s\n' "${target}" "${builder}" "missing" "missing"
    fi
  done

  echo "Builder total: $(format_bytes "${total_all}")"
  echo

  BUILDER_TOTAL_BYTES="${total_all}"
  DOMINANT_TARGET="${max_target}"
  DOMINANT_TARGET_BYTES="${max_bytes}"
}

print_image_section() {
  local target image size

  echo "Local runtime images"
  printf '%-14s %-36s %-12s\n' "target" "image" "size"
  for target in "${TARGETS[@]}"; do
    image="${IMAGES[$target]}"
    size="$(image_size_bytes "${image}")"
    if [[ -n "${size}" ]]; then
      printf '%-14s %-36s %-12s\n' "${target}" "${image}" "$(format_bytes "${size}")"
    else
      printf '%-14s %-36s %-12s\n' "${target}" "${image}" "missing"
    fi
  done
  echo
}

print_dangling_section() {
  local count bytes
  count="$(dangling_image_count)"
  bytes="$(dangling_image_bytes)"

  echo "Dangling images"
  echo "Count: ${count}"
  echo "Total: $(format_bytes "${bytes}")"
  echo

  DANGLING_COUNT="${count}"
  DANGLING_BYTES="${bytes}"
}

print_host_disk_section() {
  local root
  root="$(docker_root_dir)"

  echo "Host disk"
  if [[ -n "${root}" && -d "${root}" ]]; then
    echo "$(host_disk_line "${root}")"
  else
    echo "Docker root unavailable"
  fi
  echo

  DOCKER_ROOT="${root}"
}

print_verdict() {
  local verdict="ok"
  local message="builder cache footprint looks modest"
  local dominant_pct=0
  local disk_use=0

  if (( BUILDER_TOTAL_BYTES > 0 && DOMINANT_TARGET_BYTES > 0 )); then
    dominant_pct=$((DOMINANT_TARGET_BYTES * 100 / BUILDER_TOTAL_BYTES))
  fi

  if [[ -n "${DOCKER_ROOT}" && -d "${DOCKER_ROOT}" ]]; then
    disk_use="$(df -P "${DOCKER_ROOT}" | awk 'NR == 2 { gsub(/%/, "", $5); print $5 + 0 }')"
  fi

  if (( DOMINANT_TARGET_BYTES >= 8 * 1024 * 1024 * 1024 && dominant_pct >= 60 )); then
    verdict="prune ${DOMINANT_TARGET} soon"
    message="${DOMINANT_TARGET} builder dominates cache ($(format_bytes "${DOMINANT_TARGET_BYTES}") of $(format_bytes "${BUILDER_TOTAL_BYTES}"))"
  elif (( BUILDER_TOTAL_BYTES >= 4 * 1024 * 1024 * 1024 || DANGLING_BYTES >= 1024 * 1024 * 1024 || disk_use >= 85 )); then
    verdict="watch"
    message="cache or host disk is trending high; dominant builder is ${DOMINANT_TARGET:-none}"
  fi

  echo "Verdict: ${verdict}"
  echo "Why: ${message}"
}

main() {
  print_header
  print_builder_section
  print_image_section
  print_dangling_section
  print_host_disk_section
  print_verdict
}

main "$@"
