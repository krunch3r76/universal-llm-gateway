#!/usr/bin/env bash
# Box-side MCP stdio launcher for Grok Bot sand connectors.
# Header order matches libs/grokbot_box_mcp/headers.py BOX_HEADER_CANDIDATES.
set -euo pipefail

MCP_URL="${1:-}"
if [[ -z "${MCP_URL}" ]]; then
  echo "usage: grokbot-mcp-remote-launcher.sh <mcp-url>" >&2
  exit 2
fi

export HTTPS_PROXY="${HTTPS_PROXY:-http://127.0.0.1:8791}"
_pin_require="--require /home/box/.proxyshim/preload.cjs"
if [[ -z "${NODE_OPTIONS:-}" ]]; then
  export NODE_OPTIONS="${_pin_require}"
elif [[ "${NODE_OPTIONS}" != *"${_pin_require}"* ]]; then
  export NODE_OPTIONS="${NODE_OPTIONS} ${_pin_require}"
fi

# Keep in sync with grokbot_box_mcp.headers.BOX_HEADER_CANDIDATES (see test_launcher_shell.py).
CANDIDATES=(
  /home/box/.gateway/vortex-mcp-headers
  /home/box/sand-data/vortex-mcp-headers
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

MCP_REMOTE_PIN="0.8.6"
if [[ -n "${GROKBOT_MCP_REMOTE_VERSION:-}" && "${GROKBOT_MCP_REMOTE_VERSION}" != "${MCP_REMOTE_PIN}" ]]; then
  echo "grokbot-mcp-remote-launcher: GROKBOT_MCP_REMOTE_VERSION=${GROKBOT_MCP_REMOTE_VERSION} != pin ${MCP_REMOTE_PIN}" >&2
  exit 2
fi

MCP_REMOTE_CANDIDATES=(
  "${HOME}/.local/share/ulg-mcp-remote/${MCP_REMOTE_PIN}/node_modules/.bin/mcp-remote"
  "${HOME}/.local/bin/mcp-remote"
)

_mcp_remote_version_ok() {
  local bin="$1"
  local pkg_root
  pkg_root="$(cd "$(dirname "${bin}")/.." && pwd)"
  if [[ -f "${pkg_root}/package.json" ]]; then
    local ver
    ver="$(grep -E '"version"[[:space:]]*:' "${pkg_root}/package.json" | head -1 | sed -E 's/.*"version"[[:space:]]*:[[:space:]]*"([^"]+)".*/\1/')"
    [[ "${ver}" == "${MCP_REMOTE_PIN}" ]]
    return
  fi
  return 1
}

MCP_BIN=""
for cand in "${MCP_REMOTE_CANDIDATES[@]}"; do
  if [[ -x "${cand}" ]] && _mcp_remote_version_ok "${cand}"; then
    MCP_BIN="${cand}"
    break
  fi
done

if [[ -z "${MCP_BIN}" ]]; then
  echo "grokbot-mcp-remote-launcher: mcp-remote @${MCP_REMOTE_PIN} not installed or wrong version in box" >&2
  echo "Install: grokbot-box-mcp-install.sh (no npx over proxy)." >&2
  exit 2
fi

exec "${MCP_BIN}" "${MCP_URL}" --transport http-only --header-file "${HEADER}"
