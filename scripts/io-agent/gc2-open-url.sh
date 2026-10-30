#!/usr/bin/env bash
# Open URLs for Grok Bot agent Xvfb houses (gc1 :9, gc2 :10) via isolated Chromium.
# Used as BROWSER= and bin/xdg-open on PATH for io-agent-grok*.service units.
set -euo pipefail

URL="${1:-}"
if [[ -z "${URL}" ]]; then
  echo "usage: gc2-open-url.sh <url>" >&2
  exit 2
fi

DISPLAY="${DISPLAY:-:10}"
export DISPLAY

GC2_DATA_DIR="${GC2_DATA_DIR:-${GROK_AGENT_DATA_DIR:-/home/io/.gateway/grok-io-gc2}}"
CHROME_PROFILE="${GC2_CHROME_PROFILE:-${GROK_CHROME_PROFILE:-${GC2_DATA_DIR}/chrome-auth-profile}}"
GROK_BIN="${GROK_BIN:-/opt/Grok Bot/grok-bot}"

resolve_chrome_bin() {
  if [[ -n "${GC2_CHROME_BIN:-${IO_AGENT_CHROME_BIN:-}}" ]]; then
    echo "${GC2_CHROME_BIN:-${IO_AGENT_CHROME_BIN}}"
    return
  fi
  local _pw_cache="${PLAYWRIGHT_BROWSERS_PATH:-${HOME}/.cache/ms-playwright}"
  ls -d "${_pw_cache}"/chromium-*/chrome-linux64/chrome 2>/dev/null | sort -V | tail -1
}

CHROME_BIN="$(resolve_chrome_bin)"

mkdir -p "${CHROME_PROFILE}"

chrome_window() {
  if [[ ! -x "${CHROME_BIN}" ]]; then
    echo "grok-agent-open-url: no Playwright Chromium (${CHROME_BIN:-empty})." >&2
    echo "Install: \$HOME/.venvs/universal/bin/python -m playwright install chromium" >&2
    exit 1
  fi
  # Same --user-data-dir merges into one Chromium; avoid Firefox tab storms on OAuth retries.
  env DISPLAY="${DISPLAY}" "${CHROME_BIN}" \
    --user-data-dir="${CHROME_PROFILE}" \
    --ozone-platform=x11 \
    --no-first-run \
    --no-default-browser-check \
    --window-size=1280,900 \
    --window-position=100,50 \
    "${URL}" >/dev/null 2>&1 &
}

case "${URL}" in
  grokbot:*|sand:*)
    exec env DISPLAY="${DISPLAY}" XDG_CONFIG_HOME="${XDG_CONFIG_HOME:-${GC2_DATA_DIR}/.config}" \
      "${GROK_BIN}" \
      --user-data-dir="${GC2_DATA_DIR}" \
      "${URL}"
    ;;
  http:*|https:*)
    chrome_window
    exit 0
    ;;
  *)
    chrome_window
    exit 0
    ;;
esac
