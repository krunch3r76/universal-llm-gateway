#!/usr/bin/env bash
# Grok Bot stdio MCP launcher for user-code (io host). Connectors register
# /home/box/bin/vortex-mcp-code.sh (jupiter/box layout); header lives in the
# active grok profile, not under /home/box/.gateway.
set -euo pipefail

HEADER_FILE="${VORTEX_MCP_HEADER_FILE:-/home/io/.gateway/grok-io-gc2/vortex-mcp-headers}"
if [[ ! -f "${HEADER_FILE}" ]]; then
  HEADER_FILE="/home/io/.gateway/grok-io/vortex-mcp-headers"
fi

export PATH="/usr/local/bin:/usr/bin:/bin:${PATH:-}"

exec npx -y mcp-remote@0.8.6 https://mcp.k-1.me/mcp/code/ \
  --transport http-only \
  --header-file "${HEADER_FILE}"
