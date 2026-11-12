#!/usr/bin/env bash
# One-time io host prep: /home/box/bin/vortex-mcp-{code,toys}.sh for Grok MCP.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SRC_DIR="$(dirname "$0")"
BOX_BIN="/home/box/bin"

if [[ ! -d /home/box ]]; then
  echo "ERROR: /home/box missing. Run once as root:" >&2
  echo "  sudo mkdir -p /home/box/bin && sudo chown -R $(id -un):$(id -gn) /home/box" >&2
  exit 1
fi

install -d -m 0755 "${BOX_BIN}"
install -m 0755 "${SRC_DIR}/vortex-mcp-code.sh" "${BOX_BIN}/vortex-mcp-code.sh"
install -m 0755 "${SRC_DIR}/vortex-mcp-toys.sh" "${BOX_BIN}/vortex-mcp-toys.sh"

if [[ ! -f /home/io/.gateway/grok-io-gc2/vortex-mcp-headers ]]; then
  if [[ -f /home/io/.gateway/grok-io/vortex-mcp-headers ]]; then
    install -m 0600 /home/io/.gateway/grok-io/vortex-mcp-headers \
      /home/io/.gateway/grok-io-gc2/vortex-mcp-headers
  fi
fi

echo "Installed:"
ls -la "${BOX_BIN}/vortex-mcp-code.sh" "${BOX_BIN}/vortex-mcp-toys.sh"
echo "Verify (optional): ${BOX_BIN}/vortex-mcp-code.sh </dev/null & sleep 2; kill %1 2>/dev/null || true"
