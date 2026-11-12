#!/usr/bin/env bash
# Run inside the Grok Bot sand box: self-contained MCP launcher + mcp-remote install.
# Paste delivery: run from repo/host with --print and paste the emitted script into the box Shell.
# The bearer token must never appear in argv or shell history — use stdin or --prompt (read -rs).
set -euo pipefail

_SELF_CAND="${BASH_SOURCE[0]:-$0}"
if [[ "${_SELF_CAND}" == -* ]]; then
  _SELF_CAND="${_SELF_CAND#-}"
fi
SELF=""
SELF_DIR="${HOME}/.local/bin"
if [[ -n "${_SELF_CAND}" && "${_SELF_CAND}" != bash && "${_SELF_CAND}" != /bin/bash ]]; then
  _self_dir="$(dirname "${_SELF_CAND}")"
  if [[ -f "${_SELF_CAND}" || -f "${_self_dir}/$(basename "${_SELF_CAND}")" ]]; then
    SELF="$(cd "${_self_dir}" && pwd)/$(basename "${_SELF_CAND}")"
    SELF_DIR="$(dirname "${SELF}")"
  fi
fi
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

Token hygiene: never pass the bearer token on the command line. Pipe one header line on stdin:
  grokbot-box-mcp-install.sh --header-stdin   # paste/type line, then Ctrl-D
EOF
  exit 2
}

_installer_body_for_embed() {
  local src="${SELF}"
  if [[ -z "${src}" || ! -f "${src}" ]]; then
    src="${SELF_DIR}/grokbot-box-mcp-install.sh"
  fi
  if [[ ! -f "${src}" ]]; then
    echo "grokbot-box-mcp-install: --print requires readable install script on disk" >&2
    return 1
  fi
  if [[ -f "${LAUNCHER_SRC}" ]] && command -v python3 >/dev/null 2>&1; then
    GROKBOT_LAUNCHER_SRC="${LAUNCHER_SRC}" GROKBOT_INSTALL_SELF="${src}" python3 <<'PY'
import os
import pathlib
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
    return 0
  fi
  cat "${src}"
}

_print_pasteable() {
  local inst_tmp lch_tmp
  inst_tmp="$(mktemp)"
  lch_tmp="$(mktemp)"
  if ! _installer_body_for_embed >"${inst_tmp}"; then
    rm -f "${inst_tmp}" "${lch_tmp}"
    return 1
  fi
  if [[ -f "${LAUNCHER_SRC}" ]]; then
    cp "${LAUNCHER_SRC}" "${lch_tmp}"
  else
    : >"${lch_tmp}"
  fi
  if command -v python3 >/dev/null 2>&1; then
    python3 - "${inst_tmp}" "${lch_tmp}" <<'PY'
import pathlib
import sys

inst_path = pathlib.Path(sys.argv[1])
lch_path = pathlib.Path(sys.argv[2])
installer = inst_path.read_text(encoding="utf-8")
launcher = lch_path.read_text(encoding="utf-8") if lch_path.stat().st_size else ""
if not installer.strip():
    sys.stderr.write("grokbot-box-mcp-install: --print empty installer body\n")
    raise SystemExit(2)
marker_i = "GROKBOT_BOX_MCP_INSTALLER_EMBED"
marker_l = "GROKBOT_BOX_MCP_LAUNCHER_EMBED"
out = f"""#!/usr/bin/env bash
# Paste into the Grok Bot box Shell: writes ~/.local/bin installers, then run grokbot-box-mcp-install.sh.
(
set -euo pipefail
BIN_DIR="${{HOME}}/.local/bin"
mkdir -p "${{BIN_DIR}}"
INSTALLER="${{BIN_DIR}}/grokbot-box-mcp-install.sh"
LAUNCHER="${{BIN_DIR}}/grokbot-mcp-remote-launcher.sh"
cat >"${{INSTALLER}}" <<'{marker_i}'
{installer.rstrip(chr(10))}
{marker_i}
chmod 700 "${{INSTALLER}}"
"""
if launcher.strip():
    out += f"""cat >"${{LAUNCHER}}" <<'{marker_l}'
{launcher.rstrip(chr(10))}
{marker_l}
chmod 755 "${{LAUNCHER}}"
"""
out += 'echo "grokbot-box-mcp-install: wrote ${INSTALLER}" >&2\n'
out += ')\n'
sys.stdout.write(out)
PY
    rm -f "${inst_tmp}" "${lch_tmp}"
    return 0
  fi
  rm -f "${inst_tmp}" "${lch_tmp}"
  echo "grokbot-box-mcp-install: --print requires python3" >&2
  return 2
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
    if [[ "${LAUNCHER_SRC}" -ef "${LAUNCHER_DEST}" ]]; then
      return 0
    fi
    install -m 755 "${LAUNCHER_SRC}" "${LAUNCHER_DEST}"
  else
    _install_embedded_launcher
  fi
}

_validate_header_line() {
  local line="$1"
  line="${line%$'\r'}"
  if [[ -z "${line//[[:space:]]/}" ]]; then
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
  GROKBOT_HEADER_TMP="${tmp}"
  trap 'rm -f "${GROKBOT_HEADER_TMP:-}"; unset GROKBOT_HEADER_TMP' EXIT
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
      trap - EXIT
      unset GROKBOT_HEADER_TMP
      return 0
    fi
    if [[ ! -f "${HEADER_DEST}.bak" ]]; then
      cp -p "${HEADER_DEST}" "${HEADER_DEST}.bak"
      chmod 600 "${HEADER_DEST}.bak"
    fi
    if [[ "${HEADER_REPLACE}" != true ]]; then
      echo "grokbot-box-mcp-install: existing header differs; use --replace to overwrite (backup at .bak)" >&2
      rm -f "${tmp}"
      trap - EXIT
      unset GROKBOT_HEADER_TMP
      exit 2
    fi
  fi
  if ! mv -f "${tmp}" "${HEADER_DEST}"; then
    echo "grokbot-box-mcp-install: failed to install header at ${HEADER_DEST}" >&2
    exit 1
  fi
  trap - EXIT
  unset GROKBOT_HEADER_TMP
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
    IFS= read -r line || true
    if IFS= read -r extra; then
      echo "grokbot-box-mcp-install: refuse multi-line header input" >&2
      exit 2
    fi
    line="${line%$'\r'}"
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
      ver="$(NODE_OPTIONS='' node -p "require('${pkg}').version" 2>/dev/null || true)"
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
    if ! NODE_OPTIONS='' npm init -y; then
      echo "grokbot-box-mcp-install: npm init failed under ${INSTALL_ROOT}" >&2
      exit 1
    fi
    if ! HTTPS_PROXY="${HTTPS_PROXY}" NODE_OPTIONS='' npm install --no-save "mcp-remote@${PIN}"; then
      echo "grokbot-box-mcp-install: npm install mcp-remote@${PIN} failed" >&2
      exit 1
    fi
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
