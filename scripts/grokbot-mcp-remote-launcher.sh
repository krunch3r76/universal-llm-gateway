#!/usr/bin/env bash
# Box-side MCP stdio launcher for Grok Bot sand connectors.
# Resolves --header-file from sand-data (Grok rewrites .gateway paths there).
set -euo pipefail

MCP_URL="${1:-}"
if [[ -z "${MCP_URL}" ]]; then
  echo "usage: grokbot-mcp-remote-launcher.sh <mcp-url>" >&2
  exit 2
fi

export HTTPS_PROXY="${HTTPS_PROXY:-http://127.0.0.1:8791}"
export NODE_OPTIONS="${NODE_OPTIONS:---require /home/box/.proxyshim/preload.cjs}"

CANDIDATES=(
  /home/box/sand-data/vortex-mcp-headers
  /home/box/.gateway/vortex-mcp-headers
  /home/box/.vortex-mcp-headers
)

HEADER=""
CHECKED=()
for path in "${CANDIDATES[@]}"; do
  CHECKED+=("${path}")
  if [[ -f "${path}" ]]; then
    HEADER="${path}"
    break
  fi
done

if [[ -z "${HEADER}" ]]; then
  echo "grokbot-mcp-remote-launcher: no MCP header file found; checked:" >&2
  for path in "${CHECKED[@]}"; do
    echo "  - ${path}" >&2
  done
  exit 2
fi

MCP_REMOTE_PIN="${GROKBOT_MCP_REMOTE_VERSION:-0.8.6}"
MCP_REMOTE_CANDIDATES=(
  "${HOME}/.local/share/ulg-mcp-remote/${MCP_REMOTE_PIN}/node_modules/.bin/mcp-remote"
  "${HOME}/.local/bin/mcp-remote"
  mcp-remote
)

MCP_BIN=""
for cand in "${MCP_REMOTE_CANDIDATES[@]}"; do
  if [[ "${cand}" == */* ]]; then
    if [[ -x "${cand}" ]]; then
      MCP_BIN="${cand}"
      break
    fi
  elif command -v "${cand}" >/dev/null 2>&1; then
    MCP_BIN="$(command -v "${cand}")"
    break
  fi
done

if [[ -z "${MCP_BIN}" ]]; then
  echo "grokbot-mcp-remote-launcher: mcp-remote @${MCP_REMOTE_PIN} not installed in box" >&2
  echo "Install under ~/.local/share/ulg-mcp-remote/${MCP_REMOTE_PIN} (no npx over proxy)." >&2
  exit 2
fi

exec "${MCP_BIN}" "${MCP_URL}" --transport http-only --header-file "${HEADER}"
