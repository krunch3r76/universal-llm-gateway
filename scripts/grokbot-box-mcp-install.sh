#!/usr/bin/env bash
# Run inside the Grok Bot sand box: install launcher + optional pinned mcp-remote.
set -euo pipefail

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAUNCHER_SRC="${GROKBOT_LAUNCHER_SRC:-${SELF_DIR}/grokbot-mcp-remote-launcher.sh}"
PIN="0.8.6"
INSTALL_ROOT="${HOME}/.local/share/ulg-mcp-remote/${PIN}"
BIN_DIR="${HOME}/.local/bin"
LAUNCHER_DEST="${BIN_DIR}/grokbot-mcp-remote-launcher.sh"
HEADER_DEST="/home/box/.gateway/vortex-mcp-headers"

usage() {
  echo "usage: grokbot-box-mcp-install.sh [--header-stdin] [--skip-mcp-remote]" >&2
  exit 2
}

HEADER_STDIN=false
SKIP_MCP=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --header-stdin) HEADER_STDIN=true; shift ;;
    --skip-mcp-remote) SKIP_MCP=true; shift ;;
    -h|--help) usage ;;
    *) echo "unknown arg: $1" >&2; usage ;;
  esac
done

if [[ ! -f "${LAUNCHER_SRC}" ]]; then
  echo "grokbot-box-mcp-install: launcher source missing: ${LAUNCHER_SRC}" >&2
  exit 2
fi

mkdir -p "${BIN_DIR}"
install -m 755 "${LAUNCHER_SRC}" "${LAUNCHER_DEST}"

if [[ "${HEADER_STDIN}" == true ]]; then
  mkdir -p "$(dirname "${HEADER_DEST}")"
  umask 077
  cat >"${HEADER_DEST}"
  chmod 600 "${HEADER_DEST}"
fi

if [[ "${SKIP_MCP}" == false ]]; then
  MCP_BIN="${INSTALL_ROOT}/node_modules/.bin/mcp-remote"
  if [[ ! -x "${MCP_BIN}" ]]; then
    if ! command -v npm >/dev/null 2>&1; then
      echo "grokbot-box-mcp-install: npm missing; install mcp-remote @${PIN} manually under ${INSTALL_ROOT}" >&2
      exit 2
    fi
    mkdir -p "${INSTALL_ROOT}"
    (
      cd "${INSTALL_ROOT}"
      npm init -y >/dev/null 2>&1
      npm install --no-save "mcp-remote@${PIN}" >/dev/null
    )
  fi
fi

echo "grokbot-box-mcp-install: ok launcher=${LAUNCHER_DEST}"
