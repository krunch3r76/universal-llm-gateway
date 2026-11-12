#!/usr/bin/env bash
# Proxyshim preload + undici install (sourced by grokbot-box-mcp-install.sh or written by --print).
# Expects: PROXYSHIM_DIR, PROXYSHIM_PRELOAD_SRC, PROXYSHIM_PKG_SRC, PROXYSHIM_PRELOAD_SHA, SELF, SELF_DIR.

grokbot_proxyshim_file_sha256() {
  sha256sum "$1" | awk '{print $1}'
}

grokbot_proxyshim_embed_usable() {
  local prov="${PROXYSHIM_PROVISION_SRC:-}"
  if [[ -z "${prov}" || ! -f "${prov}" ]]; then
    prov="${BASH_SOURCE[0]}"
  fi
  [[ -f "${prov}" ]] && grep -q "GROKBOT_PROXYSHIM_PRELOAD_EMBED" "${prov}"
}

grokbot_proxyshim_install_from_embed() {
  cat >"${PROXYSHIM_DIR}/preload.cjs" <<'GROKBOT_PROXYSHIM_PRELOAD_EMBED'
const proxy = process.env.HTTPS_PROXY || process.env.https_proxy;
if (proxy) { const { setGlobalDispatcher, ProxyAgent } = require('/home/box/.proxyshim/node_modules/undici'); setGlobalDispatcher(new ProxyAgent(proxy)); }
GROKBOT_PROXYSHIM_PRELOAD_EMBED
  cat >"${PROXYSHIM_DIR}/package.json" <<'GROKBOT_PROXYSHIM_PACKAGE_EMBED'
{"name":"proxyshim","version":"1.0.0","private":true,"dependencies":{"undici":"^6.29.0"}}
GROKBOT_PROXYSHIM_PACKAGE_EMBED
}

grokbot_proxyshim_undici_loadable() {
  if ! command -v node >/dev/null 2>&1; then
    return 1
  fi
  (
    cd "${PROXYSHIM_DIR}" || exit 1
    NODE_OPTIONS='' node -e "require('undici')" >/dev/null 2>&1
  )
}

grokbot_proxyshim_install_deps() {
  if grokbot_proxyshim_undici_loadable; then
    return 0
  fi
  if ! command -v npm >/dev/null 2>&1; then
    echo "grokbot-box-mcp-install: npm missing; cannot install undici under ${PROXYSHIM_DIR}" >&2
    exit 2
  fi
  (
    cd "${PROXYSHIM_DIR}"
    if ! NODE_OPTIONS='' HTTPS_PROXY="${HTTPS_PROXY:-http://127.0.0.1:8791}" npm install --no-fund --no-audit; then
      echo "grokbot-box-mcp-install: npm install undici under proxyshim failed" >&2
      exit 1
    fi
  )
  if ! grokbot_proxyshim_undici_loadable; then
    echo "grokbot-box-mcp-install: undici install completed but require('undici') still fails under ${PROXYSHIM_DIR}" >&2
    exit 1
  fi
}

grokbot_proxyshim_atomic_install_file() {
  local src="$1" dest="$2" expected_sha="$3"
  local dest_dir
  dest_dir="$(dirname "${dest}")"
  mkdir -p "${dest_dir}"
  if [[ -L "${dest}" ]]; then
    rm -f "${dest}"
  fi
  local tmp
  tmp="$(mktemp -p "${dest_dir}" ".$(basename "${dest}").XXXXXX")"
  if ! cp "${src}" "${tmp}"; then
    rm -f "${tmp}"
    echo "grokbot-box-mcp-install: failed to stage ${dest}" >&2
    return 1
  fi
  local got
  got="$(grokbot_proxyshim_file_sha256 "${tmp}")"
  if [[ -n "${expected_sha}" && "${got}" != "${expected_sha}" ]]; then
    rm -f "${tmp}"
    echo "grokbot-box-mcp-install: proxyshim preload sha mismatch (got ${got}, want ${expected_sha})" >&2
    return 1
  fi
  mv -f "${tmp}" "${dest}"
}

grokbot_install_proxyshim() {
  export HTTPS_PROXY="${HTTPS_PROXY:-http://127.0.0.1:8791}"
  local dest_preload="${PROXYSHIM_DIR}/preload.cjs"
  local expected="${PROXYSHIM_PRELOAD_SHA}"

  if [[ -f "${dest_preload}" && ! -L "${dest_preload}" ]]; then
    local cur
    cur="$(grokbot_proxyshim_file_sha256 "${dest_preload}")"
    if [[ "${cur}" == "${expected}" ]]; then
      echo "grokbot-box-mcp-install: proxyshim preload unchanged (skip ${dest_preload})" >&2
      grokbot_proxyshim_install_deps
      return 0
    fi
  fi

  if [[ -f "${PROXYSHIM_PRELOAD_SRC}" && -f "${PROXYSHIM_PKG_SRC}" ]]; then
    mkdir -p "${PROXYSHIM_DIR}"
    if ! grokbot_proxyshim_atomic_install_file "${PROXYSHIM_PRELOAD_SRC}" "${dest_preload}" "${expected}"; then
      exit 1
    fi
    grokbot_proxyshim_atomic_install_file "${PROXYSHIM_PKG_SRC}" "${PROXYSHIM_DIR}/package.json" ""
  elif grokbot_proxyshim_embed_usable; then
    mkdir -p "${PROXYSHIM_DIR}"
    grokbot_proxyshim_install_from_embed
    local got
    got="$(grokbot_proxyshim_file_sha256 "${dest_preload}")"
    if [[ "${got}" != "${expected}" ]]; then
      echo "grokbot-box-mcp-install: proxyshim preload sha mismatch (got ${got}, want ${expected})" >&2
      exit 1
    fi
  else
    echo "grokbot-box-mcp-install: missing proxyshim preload source (expected ${PROXYSHIM_PRELOAD_SRC})" >&2
    exit 1
  fi
  grokbot_proxyshim_install_deps
}
