#!/usr/bin/env bash
# Slot-agnostic URL opener for io-agent Xvfb houses — routes http(s) to house Chromium on X11.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
SCRIPT_NAME="$(basename "${BASH_SOURCE[0]}")"

usage() {
  echo "usage: ${SCRIPT_NAME} <url-or-path>" >&2
}

run_child() {
  env -u WAYLAND_DISPLAY DISPLAY="${HOUSE_DISPLAY}" "$@"
}

child_wayland_display_label() {
  if run_child printenv WAYLAND_DISPLAY >/dev/null 2>&1; then
    run_child printenv WAYLAND_DISPLAY
  else
    echo "unset"
  fi
}

log_line() {
  local route="$1"
  local url_key="$2"
  [[ -z "${IO_AGENT_OPEN_URL_LOG:-}" ]] && return 0
  local ts log_parent
  ts="$(date -Iseconds)"
  log_parent="$(dirname "${IO_AGENT_OPEN_URL_LOG}")"
  {
    mkdir -p "${log_parent}" 2>/dev/null || true
    printf '%s display=%s route=%s url=%s\n' "${ts}" "${HOUSE_DISPLAY:-}" "${route}" "${url_key}" \
      >>"${IO_AGENT_OPEN_URL_LOG}"
  } 2>/dev/null || true
}

reduced_url_key() {
  local raw="$1"
  case "${raw}" in
    http://*|https://*)
      local scheme="${raw%%://*}"
      local rest="${raw#*://}"
      rest="${rest%%#*}"
      rest="${rest%%\?*}"
      local authority path_part="${rest#*/}"
      if [[ "${rest}" == */* ]]; then
        authority="${rest%%/*}"
        path_part="/${path_part}"
      else
        authority="${rest}"
        path_part="/"
      fi
      authority="${authority##*@}"
      echo "${scheme}://${authority}${path_part}"
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
  local entry entry_phys
  local IFS=:
  for entry in ${PATH}; do
    [[ -z "${entry}" ]] && continue
    entry_phys="$(cd "${entry}" 2>/dev/null && pwd -P)" || continue
    [[ "${entry_phys}" == "${SCRIPT_DIR}" ]] && continue
    if [[ -x "${entry}/xdg-open" ]]; then
      echo "${entry}/xdg-open"
      return 0
    fi
  done
  return 1
}

surface_browser_window() {
  local class="$1"
  local display="${HOUSE_DISPLAY}"
  local log_path="${IO_AGENT_OPEN_URL_LOG:-}"
  setsid bash -c "$(cat <<'SURFACE_EOF'
surface_poll() {
  local class="$1" display="$2" log_path="$3"
  command -v xdotool >/dev/null 2>&1 || return 0
  local wid="" end=$((SECONDS + 15))
  while (( SECONDS < end )); do
    if command -v timeout >/dev/null 2>&1; then
      wid="$(env -u WAYLAND_DISPLAY DISPLAY="${display}" timeout 0.4 xdotool search --onlyvisible --class "${class}" 2>/dev/null | tail -1 || true)"
    else
      wid="$(env -u WAYLAND_DISPLAY DISPLAY="${display}" xdotool search --onlyvisible --class "${class}" 2>/dev/null | tail -1 || true)"
    fi
    if [[ -n "${wid}" ]]; then
      if command -v timeout >/dev/null 2>&1; then
        env -u WAYLAND_DISPLAY DISPLAY="${display}" timeout 0.4 xdotool windowraise "${wid}" 2>/dev/null || true
        env -u WAYLAND_DISPLAY DISPLAY="${display}" timeout 0.4 xdotool windowfocus "${wid}" 2>/dev/null || true
      else
        env -u WAYLAND_DISPLAY DISPLAY="${display}" xdotool windowraise "${wid}" 2>/dev/null || true
        env -u WAYLAND_DISPLAY DISPLAY="${display}" xdotool windowfocus "${wid}" 2>/dev/null || true
      fi
      return 0
    fi
    sleep 0.5
  done
  if [[ -n "${log_path}" ]]; then
    {
      printf '%s display=%s route=surface-miss class=%s\n' "$(date -Iseconds)" "${display}" "${class}" \
        >>"${log_path}"
    } 2>/dev/null || true
  fi
}
surface_poll "$@"
SURFACE_EOF
)" _ "${class}" "${display}" "${log_path}" </dev/null >/dev/null 2>&1 &
  disown -h 2>/dev/null || disown 2>/dev/null || true
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
    if [[ ! -x "${CHROME_BIN}" ]]; then
      echo "io-agent-open-url: no Playwright Chromium (${CHROME_BIN:-empty})." >&2
      echo "Install: \$HOME/.venvs/universal/bin/python -m playwright install chromium" >&2
      log_line "error:no-chromium" "${URL_KEY}"
      exit 1
    fi
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
    WAYLAND_PRINT="$(child_wayland_display_label)"
    if [[ "${DRY}" == "1" ]]; then
      echo "route=house-browser"
      echo "display=${HOUSE_DISPLAY}"
      echo "wayland_display=${WAYLAND_PRINT}"
      echo "argv=$(quote_argv "${CHROME_ARGS[@]}")"
      log_line "house-browser" "${URL_KEY}"
      exit 0
    fi
    setsid env -u WAYLAND_DISPLAY DISPLAY="${HOUSE_DISPLAY}" "${CHROME_ARGS[@]}" \
      </dev/null >/dev/null 2>&1 &
    disown -h 2>/dev/null || disown 2>/dev/null || true
    surface_browser_window "${BROWSER_CLASS}"
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
  # fallthrough for delegate — check chrome not required
  WAYLAND_PRINT="$(child_wayland_display_label)"
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