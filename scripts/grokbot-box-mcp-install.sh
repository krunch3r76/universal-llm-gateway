#!/usr/bin/env bash
# Run inside the Grok Bot sand box: self-contained MCP launcher + mcp-remote install.
# Paste delivery: run from repo/host with --print and paste the emitted script into the box Shell.
# The bearer token must never appear in argv or shell history — use stdin or --prompt (read -rs).
set -euo pipefail

SELF="${BASH_SOURCE[0]}"
SELF_DIR="$(cd "$(dirname "${SELF}")" && pwd)"
LAUNCHER_SRC="${GROKBOT_LAUNCHER_SRC:-${SELF_DIR}/grokbot-mcp-remote-launcher.sh}"
PIN="0.8.6"
INSTALL_ROOT="${HOME}/.local/share/ulg-mcp-remote/${PIN}"
BIN_DIR="${HOME}/.local/bin"
LAUNCHER_DEST="${BIN_DIR}/grokbot-mcp-remote-launcher.sh"
HEADER_DEST="${GROKBOT_HEADER_DEST:-/home/box/.gateway/vortex-mcp-headers}"
_PROXYSHIM_REQUIRE="--require /home/box/.proxyshim/preload.cjs"

usage() {
  cat >&2 <<'EOF'
usage: grokbot-box-mcp-install.sh [--print] [--header-stdin] [--prompt] [--replace]
                                 [--skip-mcp-remote] [--help]

  --print           Emit this script for paste into the box Grok Bot Shell.
  --header-stdin    Read one Authorization header line from stdin (not a TTY).
  --prompt          Read header with read -rs (no shell history); for interactive box.
  --replace         Overwrite an existing header that differs (backs up to .bak first).
  --skip-mcp-remote Skip npm install of mcp-remote@0.8.6.

Token hygiene: never pass the bearer token on the command line. Pipe one line:
  printf '%s\n' 'Authorization: Bearer <secret>' | grokbot-box-mcp-install.sh --header-stdin
EOF
  exit 2
}

_print_pasteable() {
  if [[ -f "${LAUNCHER_SRC}" ]] && command -v python3 >/dev/null 2>&1; then
    GROKBOT_LAUNCHER_SRC="${LAUNCHER_SRC}" GROKBOT_INSTALL_SELF="${SELF}" python3 <<'PY'
import os
import pathlib
import re
import sys

self_path = pathlib.Path(os.environ["GROKBOT_INSTALL_SELF"]).resolve()
launcher = pathlib.Path(os.environ["GROKBOT_LAUNCHER_SRC"]).read_text(encoding="utf-8")
body = self_path.read_text(encoding="utf-8")
start = "  cat >\"${LAUNCHER_DEST}\" <<'GROKBOT_MCP_REMOTE_LAUNCHER_EMBED'\n"
end = "\nGROKBOT_MCP_REMOTE_LAUNCHER_EMBED\n"
i = body.find(start)
j = body.find(end, i)
if i < 0 or j < 0:
    sys.stderr.write("grokbot-box-mcp-install: --print embed markers missing\n")
    raise SystemExit(2)
new_body = body[: i + len(start)] + launcher.rstrip("\n") + body[j:]
sys.stdout.write(new_body)
PY
    return
  fi
  cat "${SELF}"
}

_install_embedded_launcher() {
  cat >"${LAUNCHER_DEST}" <<'GROKBOT_MCP_REMOTE_LAUNCHER_EMBED'
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

INSTALL_ROOT="${HOME}/.local/share/ulg-mcp-remote/${MCP_REMOTE_PIN}"
MCP_BIN="${INSTALL_ROOT}/node_modules/.bin/mcp-remote"
PKG_JSON="${INSTALL_ROOT}/node_modules/mcp-remote/package.json"

_mcp_remote_version_ok() {
  if [[ ! -x "${MCP_BIN}" || ! -f "${PKG_JSON}" ]]; then
    return 1
  fi
  local ver=""
  if command -v node >/dev/null 2>&1; then
    ver="$(NODE_OPTIONS='' node -p "require('${PKG_JSON}').version" 2>/dev/null || true)"
  fi
  if [[ -z "${ver}" ]]; then
    ver="$(grep -E '"version"[[:space:]]*:' "${PKG_JSON}" | head -1 | sed -E 's/.*"version"[[:space:]]*:[[:space:]]*"([^"]+)".*/\1/')"
  fi
  [[ -n "${ver}" && "${ver}" == "${MCP_REMOTE_PIN}" ]]
}

if ! _mcp_remote_version_ok; then
  echo "grokbot-mcp-remote-launcher: mcp-remote @${MCP_REMOTE_PIN} not installed or wrong version in box" >&2
  echo "Install: grokbot-box-mcp-install.sh (no npx over proxy)." >&2
  exit 2
fi

exec "${MCP_BIN}" "${MCP_URL}" --transport http-only --header-file "${HEADER}"
GROKBOT_MCP_REMOTE_LAUNCHER_EMBED
  chmod 755 "${LAUNCHER_DEST}"
}

_install_launcher() {
  mkdir -p "${BIN_DIR}"
  if [[ -f "${LAUNCHER_SRC}" ]]; then
    install -m 755 "${LAUNCHER_SRC}" "${LAUNCHER_DEST}"
  else
    _install_embedded_launcher
  fi
}

_validate_header_line() {
  local line="$1"
  if [[ -z "${line}" ]]; then
    echo "grokbot-box-mcp-install: empty header line" >&2
    return 1
  fi
  if [[ "${line}" == *"<token>"* ]]; then
    echo "grokbot-box-mcp-install: refuse placeholder <token>" >&2
    return 1
  fi
  local _hdr_re='^Authorization: Bearer [^ <>]{20,}$'
  if ! [[ "${line}" =~ ${_hdr_re} ]]; then
    echo "grokbot-box-mcp-install: header must match ^Authorization: Bearer [^ <>]{20,}\$" >&2
    return 1
  fi
  return 0
}

_write_header_from_line() {
  local line="$1"
  if ! _validate_header_line "${line}"; then
    exit 2
  fi
  mkdir -p "$(dirname "${HEADER_DEST}")"
  local tmp
  tmp="$(mktemp -p "$(dirname "${HEADER_DEST}")" .vortex-mcp-headers.XXXXXX)"
  local prior_umask
  prior_umask="$(umask)"
  umask 077
  printf '%s\n' "${line}" >"${tmp}"
  chmod 600 "${tmp}"
  umask "${prior_umask}"

  local new_body
  new_body="$(cat "${tmp}")"
  if [[ -f "${HEADER_DEST}" ]]; then
    local old_body
    old_body="$(cat "${HEADER_DEST}")"
    if [[ "${old_body}" == "${new_body}" ]]; then
      rm -f "${tmp}"
      return 0
    fi
    cp -p "${HEADER_DEST}" "${HEADER_DEST}.bak"
    if [[ "${HEADER_REPLACE}" != true ]]; then
      echo "grokbot-box-mcp-install: existing header differs; use --replace to overwrite (backup at .bak)" >&2
      rm -f "${tmp}"
      exit 2
    fi
  fi
  mv -f "${tmp}" "${HEADER_DEST}"
  chmod 600 "${HEADER_DEST}"
}

_read_header_line() {
  local line=""
  if [[ "${HEADER_PROMPT}" == true ]]; then
  read -rs -p "Paste MCP Authorization header line (hidden): " line
    echo >&2
  elif [[ -t 0 ]]; then
    echo "grokbot-box-mcp-install: stdin is a TTY; pipe the header line or use --prompt" >&2
    exit 2
  else
    if ! IFS= read -r line; then
      line=""
    fi
    if IFS= read -r extra; then
      echo "grokbot-box-mcp-install: refuse multi-line header input" >&2
      exit 2
    fi
  fi
  _write_header_from_line "${line}"
}

_append_proxyshim() {
  if [[ -z "${NODE_OPTIONS:-}" ]]; then
    export NODE_OPTIONS="${_PROXYSHIM_REQUIRE}"
  elif [[ "${NODE_OPTIONS}" != *"${_PROXYSHIM_REQUIRE}"* ]]; then
    export NODE_OPTIONS="${NODE_OPTIONS} ${_PROXYSHIM_REQUIRE}"
  fi
}

_install_mcp_remote() {
  export HTTPS_PROXY="${HTTPS_PROXY:-http://127.0.0.1:8791}"
  _append_proxyshim
  local mcp_bin="${INSTALL_ROOT}/node_modules/.bin/mcp-remote"
  if [[ -x "${mcp_bin}" ]]; then
    local pkg="${INSTALL_ROOT}/node_modules/mcp-remote/package.json"
    if [[ -f "${pkg}" ]] && command -v node >/dev/null 2>&1; then
      local ver
      ver="$(node -p "require('${pkg}').version" 2>/dev/null || true)"
      if [[ "${ver}" == "${PIN}" ]]; then
        return 0
      fi
    fi
  fi
  if ! command -v npm >/dev/null 2>&1; then
    echo "grokbot-box-mcp-install: npm missing; install mcp-remote @${PIN} manually under ${INSTALL_ROOT}" >&2
    exit 2
  fi
  mkdir -p "${INSTALL_ROOT}"
  (
    cd "${INSTALL_ROOT}"
    npm init -y >/dev/null 2>&1
    HTTPS_PROXY="${HTTPS_PROXY}" NODE_OPTIONS="${NODE_OPTIONS}" \
      npm install --no-save "mcp-remote@${PIN}" >/dev/null
  )
}

HEADER_STDIN=false
HEADER_PROMPT=false
HEADER_REPLACE=false
SKIP_MCP=false
PRINT=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --print) PRINT=true; shift ;;
    --header-stdin) HEADER_STDIN=true; shift ;;
    --prompt) HEADER_PROMPT=true; shift ;;
    --replace) HEADER_REPLACE=true; shift ;;
    --skip-mcp-remote) SKIP_MCP=true; shift ;;
    -h|--help) usage ;;
    *) echo "unknown arg: $1" >&2; usage ;;
  esac
done

if [[ "${PRINT}" == true ]]; then
  _print_pasteable
  exit 0
fi

_install_launcher

if [[ "${HEADER_STDIN}" == true || "${HEADER_PROMPT}" == true ]]; then
  _read_header_line
elif [[ "${HEADER_REPLACE}" == true ]]; then
  echo "grokbot-box-mcp-install: --replace requires --header-stdin or --prompt" >&2
  exit 2
fi

if [[ "${SKIP_MCP}" == false ]]; then
  _install_mcp_remote
fi

echo "grokbot-box-mcp-install: ok launcher=${LAUNCHER_DEST}"
