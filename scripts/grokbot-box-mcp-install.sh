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
PROXYSHIM_DIR="${GROKBOT_PROXYSHIM_DIR:-/home/box/.proxyshim}"
PROXYSHIM_PRELOAD_SRC="${GROKBOT_PROXYSHIM_PRELOAD_SRC:-${SELF_DIR}/grokbot-proxyshim/preload.cjs}"
PROXYSHIM_PKG_SRC="${GROKBOT_PROXYSHIM_PKG_SRC:-${SELF_DIR}/grokbot-proxyshim/package.json}"
PROXYSHIM_PROVISION_SRC="${GROKBOT_PROXYSHIM_PROVISION_SRC:-${SELF_DIR}/grokbot-proxyshim/provision.sh}"
PROXYSHIM_PRELOAD_SHA="0e8a545a2b04946b489fad332f1af485d397f77c0c7fc161e2f6386ce016636c"
_PROXYSHIM_REQUIRE="--require ${PROXYSHIM_DIR}/preload.cjs"

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
  if [[ -f "${LAUNCHER_SRC}" && -f "${PROXYSHIM_PROVISION_SRC}" ]] && command -v python3 >/dev/null 2>&1; then
    GROKBOT_LAUNCHER_SRC="${LAUNCHER_SRC}" \
    GROKBOT_PROXYSHIM_PRELOAD_SRC="${PROXYSHIM_PRELOAD_SRC}" \
    GROKBOT_PROXYSHIM_PKG_SRC="${PROXYSHIM_PKG_SRC}" \
    GROKBOT_PROXYSHIM_PROVISION_SRC="${PROXYSHIM_PROVISION_SRC}" \
    GROKBOT_INSTALL_SELF="${src}" python3 <<'PY'
import os
import pathlib
import sys

self_path = pathlib.Path(os.environ["GROKBOT_INSTALL_SELF"]).resolve()
preload = pathlib.Path(os.environ["GROKBOT_PROXYSHIM_PRELOAD_SRC"]).read_text(encoding="utf-8")
pkg = pathlib.Path(os.environ["GROKBOT_PROXYSHIM_PKG_SRC"]).read_text(encoding="utf-8")
provision = pathlib.Path(os.environ["GROKBOT_PROXYSHIM_PROVISION_SRC"]).read_text(encoding="utf-8")
body = self_path.read_text(encoding="utf-8")


def splice(body: str, start: str, end: str, content: str) -> str:
    i = body.find(start)
    j = body.find(end, i)
    if i < 0 or j < 0:
        sys.stderr.write("grokbot-box-mcp-install: --print embed markers missing\n")
        raise SystemExit(2)
    return body[: i + len(start)] + content.rstrip("\n") + body[j:]


start_p = " <<'GROKBOT_PROXYSHIM_PRELOAD_EMBED'\n"
end_p = "\nGROKBOT_PROXYSHIM_PRELOAD_EMBED\n"
start_j = " <<'GROKBOT_PROXYSHIM_PACKAGE_EMBED'\n"
end_j = "\nGROKBOT_PROXYSHIM_PACKAGE_EMBED\n"
provision = splice(provision, start_p, end_p, preload)
provision = splice(provision, start_j, end_j, pkg)
prov_out = os.environ.get("GROKBOT_EMBED_PROVISION_OUT")
if prov_out:
    pathlib.Path(prov_out).write_text(provision, encoding="utf-8")
sys.stdout.write(body)
PY
    return 0
  fi
  cat "${src}"
}

_print_pasteable() {
  local inst_tmp lch_tmp prov_tmp
  inst_tmp="$(mktemp)"
  lch_tmp="$(mktemp)"
  prov_tmp="$(mktemp)"
  if ! GROKBOT_EMBED_PROVISION_OUT="${prov_tmp}" _installer_body_for_embed >"${inst_tmp}"; then
    rm -f "${inst_tmp}" "${lch_tmp}" "${prov_tmp}"
    return 1
  fi
  if [[ ! -f "${LAUNCHER_SRC}" ]]; then
    rm -f "${inst_tmp}" "${lch_tmp}" "${prov_tmp}"
    echo "grokbot-box-mcp-install: --print missing launcher (expected ${LAUNCHER_SRC})" >&2
    return 1
  fi
  cp "${LAUNCHER_SRC}" "${lch_tmp}"
  if [[ ! -s "${prov_tmp}" && -f "${PROXYSHIM_PROVISION_SRC}" ]]; then
    cp "${PROXYSHIM_PROVISION_SRC}" "${prov_tmp}"
  fi
  if command -v python3 >/dev/null 2>&1; then
    python3 - "${inst_tmp}" "${lch_tmp}" "${prov_tmp}" <<'PY'
import pathlib
import sys

inst_path = pathlib.Path(sys.argv[1])
lch_path = pathlib.Path(sys.argv[2])
prov_path = pathlib.Path(sys.argv[3])
installer = inst_path.read_text(encoding="utf-8")
launcher = lch_path.read_text(encoding="utf-8") if lch_path.stat().st_size else ""
provision = prov_path.read_text(encoding="utf-8") if prov_path.is_file() and prov_path.stat().st_size else ""
if not installer.strip():
    sys.stderr.write("grokbot-box-mcp-install: --print empty installer body\n")
    raise SystemExit(2)
if not provision.strip():
    sys.stderr.write("grokbot-box-mcp-install: --print missing proxyshim provision script\n")
    raise SystemExit(2)
marker_i = "GROKBOT_BOX_MCP_INSTALLER_EMBED"
marker_l = "GROKBOT_BOX_MCP_LAUNCHER_EMBED"
marker_p = "GROKBOT_BOX_MCP_PROVISION_EMBED"
out = f"""#!/usr/bin/env bash
# Paste into the Grok Bot box Shell: writes ~/.local/bin installers, then run grokbot-box-mcp-install.sh.
(
set -euo pipefail
BIN_DIR="${{HOME}}/.local/bin"
mkdir -p "${{BIN_DIR}}/grokbot-proxyshim"
INSTALLER="${{BIN_DIR}}/grokbot-box-mcp-install.sh"
LAUNCHER="${{BIN_DIR}}/grokbot-mcp-remote-launcher.sh"
PROVISION="${{BIN_DIR}}/grokbot-proxyshim/provision.sh"
cat >"${{INSTALLER}}" <<'{marker_i}'
{installer.rstrip(chr(10))}
{marker_i}
chmod 700 "${{INSTALLER}}"
cat >"${{PROVISION}}" <<'{marker_p}'
{provision.rstrip(chr(10))}
{marker_p}
chmod 755 "${{PROVISION}}"
"""
if not launcher.strip():
    sys.stderr.write("grokbot-box-mcp-install: --print missing launcher body\n")
    raise SystemExit(2)
out += f"""cat >"${{LAUNCHER}}" <<'{marker_l}'
{launcher.rstrip(chr(10))}
{marker_l}
chmod 755 "${{LAUNCHER}}"
"""
out += 'echo "grokbot-box-mcp-install: wrote ${INSTALLER}" >&2\n'
out += 'echo "grokbot-box-mcp-install: run installer to provision proxyshim under /home/box/.proxyshim" >&2\n'
out += ')\n'
sys.stdout.write(out)
PY
    rm -f "${inst_tmp}" "${lch_tmp}" "${prov_tmp}"
    return 0
  fi
  rm -f "${inst_tmp}" "${lch_tmp}" "${prov_tmp}"
  echo "grokbot-box-mcp-install: --print requires python3" >&2
  return 2
}

_install_launcher() {
  mkdir -p "${BIN_DIR}"
  if [[ ! -f "${LAUNCHER_SRC}" ]]; then
    echo "grokbot-box-mcp-install: missing launcher (expected ${LAUNCHER_SRC})" >&2
    exit 1
  fi
  if [[ "${LAUNCHER_SRC}" -ef "${LAUNCHER_DEST}" ]]; then
    return 0
  fi
  install -m 755 "${LAUNCHER_SRC}" "${LAUNCHER_DEST}"
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

_load_proxyshim_provision() {
  if [[ ! -f "${PROXYSHIM_PROVISION_SRC}" ]]; then
    echo "grokbot-box-mcp-install: missing proxyshim provision script (expected ${PROXYSHIM_PROVISION_SRC})" >&2
    exit 1
  fi
  # shellcheck source=/dev/null
  source "${PROXYSHIM_PROVISION_SRC}"
}

_install_proxyshim() {
  _load_proxyshim_provision
  grokbot_install_proxyshim
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
_install_proxyshim

if [[ "${HEADER_STDIN}" == true || "${HEADER_PROMPT}" == true ]]; then
  _read_header_line
elif [[ "${HEADER_REPLACE}" == true ]]; then
  echo "grokbot-box-mcp-install: --replace requires --header-stdin or --prompt" >&2
  exit 2
fi

if [[ "${SKIP_MCP}" == false ]]; then
  _install_mcp_remote
fi

echo "grokbot-box-mcp-install: ok launcher=${LAUNCHER_DEST} proxyshim=${PROXYSHIM_DIR}"
