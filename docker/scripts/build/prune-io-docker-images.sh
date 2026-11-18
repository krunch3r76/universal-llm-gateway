#!/usr/bin/env bash
# Free disk on io before stream-loading a new GPU image (~12–20GB layer churn).
# Run ONLY on io. Interactive unless PRUNE_YES=1.
set -euo pipefail

if [[ "$(hostname -s 2>/dev/null || hostname)" != "io" ]]; then
  echo "This script is intended for host io (current: $(hostname))"
  read -rp "Continue anyway? [y/N] " a
  [[ "${a,,}" == "y" ]] || exit 0
fi

docker system df

if [[ "${PRUNE_YES:-0}" != "1" ]]; then
  echo ""
  echo "Will run: docker image prune -a -f (removes unused images, NOT images used by running containers)"
  read -rp "Proceed? [y/N] " a
  [[ "${a,,}" == "y" ]] || exit 0
fi

docker image prune -a -f
df -h /
docker system df