#!/usr/bin/env bash
# Slot-agnostic URL opener for io-agent Xvfb houses — routes http(s) to house Chromium on X11.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT_NAME="$(basename "${BASH_SOURCE[0]}")"

usage() {
  echo "usage: ${SCRIPT_NAME} <url-or-path>" >&2
}

log_line() {
  local route="$1"
  local url_key="$2"
  [[ -z "${IO_AGENT_OPEN_URL_LOG:-}" ]] && return 0
  local ts
  ts="$(date -Iseconds)"
  printf '%s display=%s route=%s url=%s\n' "${ts}" "${HOUSE_DISPLAY:-}" "${route}" "${url_key}" \
    >>"${IO_AGENT_OPEN_URL_LOG}"
}

reduced_url_key() {
  local raw="$1"
  case "${raw}" in
    http://*|https://*)
      local scheme rest authority path_only
      scheme="${raw%%://*}"
      rest="${raw#*://}"
      authority="${rest%%/*}"
      path_only="/${rest#*/}"
      path_only="${path_only%%[?#]*}"
      if [[ "${rest}" == "${authority}" ]]; then
        path_only="/"
      fi
      echo "${scheme}://${authority}${path_only}"
      ;;
    *)
      echo "${raw}"
      ;;
  esac
}

resolve_chrome_bin() {
  if [[ -n "${IO_AGENT_CHROME_BIN:-}" ]]; then
    echo "${IO_AGENT_CHROME_BIN}"
    return
  fi
  local _pw_cache="${PLAYWRIGHT_BROWSERS_PATH:-${HOME}/.cache/ms-playwright}"
  ls -d "${_pw_cache}"/chromium-*/chrome-linux64/chrome 2>/dev/null | sort -V | tail -1 || true
}

quote_argv() {
  local out="" arg
  for arg in "$@"; do
    printf -v arg '%q' "${arg}"
    out+="${arg} "
  done
  echo "${out%" "}"
}

find_delegate_xdg_open() {
  local entry dir
  local IFS=:
  for entry in ${PATH}; do
    [[ -z "${entry}" ]] && continue
    dir="$(cd "${entry}" 2>/dev/null && pwd -P)" || continue
    [[ "${dir}" == "${SCRIPT_DIR}" ]] && continue
    if [[ -x "${entry}/xdg-open" ]]; then
      echo "${entry}/xdg-open"
      return 0
    fi
  done
  return 1
}

surface_browser_window() {
  local class="$1"
  [[ "${IO_AGENT_OPEN_URL_SKIP_SURFACE:-}" == "1" ]] && return 0
  command -v xdotool >/dev/null 2>&1 || return 0
  local wid=""
  local end=$((SECONDS + 15))
  while (( SECONDS < end )); do
    if command -v timeout >/dev/null 2>&1; then
      wid="$(env -u WAYLAND_DISPLAY DISPLAY="${HOUSE_DISPLAY}" timeout 0.4 xdotool search --class "${class}" 2>/dev/null | tail -1 || true)"
    else
      wid="$(env -u WAYLAND_DISPLAY DISPLAY="${HOUSE_DISPLAY}" xdotool search --class "${class}" 2>/dev/null | tail -1 || true)"
    fi
    if [[ -n "${wid}" ]]; then
      if command -v timeout >/dev/null 2>&1; then
        env -u WAYLAND_DISPLAY DISPLAY="${HOUSE_DISPLAY}" timeout 0.4 xdotool windowraise "${wid}" 2>/dev/null || true
        env -u WAYLAND_DISPLAY DISPLAY="${HOUSE_DISPLAY}" timeout 0.4 xdotool windowfocus "${wid}" 2>/dev/null || true
      else
        env -u WAYLAND_DISPLAY DISPLAY="${HOUSE_DISPLAY}" xdotool windowraise "${wid}" 2>/dev/null || true
        env -u WAYLAND_DISPLAY DISPLAY="${HOUSE_DISPLAY}" xdotool windowfocus "${wid}" 2>/dev/null || true
      fi
      return 0
    fi
    sleep 0.5
  done
  [[ -n "${IO_AGENT_OPEN_URL_LOG:-}" ]] && \
    printf '%s display=%s route=surface-miss class=%s\n' "$(date -Iseconds)" "${HOUSE_DISPLAY}" "${class}" \
      >>"${IO_AGENT_OPEN_URL_LOG}" || true
}

URL="${1:-}"
if [[ -z "${URL}" ]]; then
  usage
  exit 2
fi
if [[ "$#" -ne 1 ]]; then
  usage
  exit 2
fi

HOUSE_DISPLAY="${IO_AGENT_DISPLAY:-${DISPLAY:-}}"
if [[ -z "${HOUSE_DISPLAY}" ]]; then
  echo "io-agent-open-url: no display (set IO_AGENT_DISPLAY or DISPLAY)" >&2
  log_line "error:no-display" "$(reduced_url_key "${URL}")"
  exit 2
fi

URL_KEY="$(reduced_url_key "${URL}")"
DRY="${IO_AGENT_OPEN_URL_DRY_RUN:-}"
# Dry-run reports the child env (Wayland cleared for Xvfb houses).
WAYLAND_PRINT="unset"

case "${URL}" in
  http:*|https:*)
    if [[ -z "${IO_AGENT_BROWSER_PROFILE:-}" ]]; then
      echo "io-agent-open-url: IO_AGENT_BROWSER_PROFILE is required" >&2
      log_line "error:missing-profile" "${URL_KEY}"
      exit 2
    fi
    mkdir -p "${IO_AGENT_BROWSER_PROFILE}"
    CHROME_BIN="$(resolve_chrome_bin)"
    BROWSER_CLASS="${IO_AGENT_BROWSER_CLASS:-io-agent-browser}"
    CHROME_ARGS=(
      "${CHROME_BIN}"
      "--user-data-dir=${IO_AGENT_BROWSER_PROFILE}"
      "--ozone-platform=x11"
      "--no-first-run"
      "--no-default-browser-check"
      "--class=${BROWSER_CLASS}"
      "--window-size=1280,900"
      "--window-position=100,50"
      "${URL}"
    )
    if [[ "${DRY}" == "1" ]]; then
      echo "route=house-browser"
      echo "display=${HOUSE_DISPLAY}"
      echo "wayland_display=${WAYLAND_PRINT}"
      echo "argv=$(quote_argv "${CHROME_ARGS[@]}")"
      log_line "house-browser" "${URL_KEY}"
      exit 0
    fi
    if [[ ! -x "${CHROME_BIN}" ]]; then
      echo "io-agent-open-url: no Playwright Chromium (${CHROME_BIN:-empty})." >&2
      echo "Install: \$HOME/.venvs/universal/bin/python -m playwright install chromium" >&2
      log_line "error:no-chromium" "${URL_KEY}"
      exit 1
    fi
    setsid env -u WAYLAND_DISPLAY DISPLAY="${HOUSE_DISPLAY}" "${CHROME_ARGS[@]}" \
      </dev/null >/dev/null 2>&1 &
    disown -h 2>/dev/null || disown 2>/dev/null || true
    surface_browser_window "${BROWSER_CLASS}" &
    disown -h 2>/dev/null || disown 2>/dev/null || true
    log_line "house-browser" "${URL_KEY}"
    exit 0
    ;;
  *)
    DELEGATE="$(find_delegate_xdg_open || true)"
    if [[ -z "${DELEGATE}" ]]; then
      echo "io-agent-open-url: no delegate xdg-open on PATH" >&2
      log_line "error:no-delegate-xdg-open" "${URL_KEY}"
      exit 1
    fi
    if [[ "${DRY}" == "1" ]]; then
      echo "route=delegate-xdg-open"
      echo "display=${HOUSE_DISPLAY}"
      echo "wayland_display=${WAYLAND_PRINT}"
      echo "argv=$(quote_argv "${DELEGATE}" "${URL}")"
      log_line "delegate-xdg-open" "${URL_KEY}"
      exit 0
    fi
    log_line "delegate-xdg-open" "${URL_KEY}"
    exec env -u WAYLAND_DISPLAY DISPLAY="${HOUSE_DISPLAY}" "${DELEGATE}" "${URL}"
    ;;
esac