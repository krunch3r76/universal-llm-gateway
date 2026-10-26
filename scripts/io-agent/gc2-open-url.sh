#!/usr/bin/env bash
# Open URLs for Grok Bot gc2 on DISPLAY=:10 with an isolated Chromium profile.
# Used as BROWSER= and via bin/xdg-open on PATH for io-agent-grok-gc2.service.
set -euo pipefail

URL="${1:-}"
if [[ -z "${URL}" ]]; then
  echo "usage: gc2-open-url.sh <url>" >&2
  exit 2
fi

DISPLAY="${DISPLAY:-:10}"
export DISPLAY

GC2_DATA_DIR="${GC2_DATA_DIR:-/home/io/.gateway/grok-io-gc2}"
CHROME_PROFILE="${GC2_CHROME_PROFILE:-${GC2_DATA_DIR}/chrome-auth-profile}"
if [[ -z "${GC2_CHROME_BIN:-}" ]]; then
  _pw_cache="${PLAYWRIGHT_BROWSERS_PATH:-${HOME}/.cache/ms-playwright}"
  _pw_chrome="$(
    ls -d "${_pw_cache}"/chromium-*/chrome-linux64/chrome 2>/dev/null | sort -V | tail -1
  )"
  CHROME_BIN="${_pw_chrome:-}"
else
  CHROME_BIN="${GC2_CHROME_BIN}"
fi
GROK_BIN="${GROK_BIN:-/opt/Grok Bot/grok-bot}"

mkdir -p "${CHROME_PROFILE}"

chrome_window() {
  if [[ ! -x "${CHROME_BIN}" ]]; then
    echo "gc2-open-url: missing chrome at ${CHROME_BIN}" >&2
    exec env DISPLAY="${DISPLAY}" MOZ_DISABLE_REMOTE=1 firefox --new-window "${URL}"
  fi
  env DISPLAY="${DISPLAY}" "${CHROME_BIN}" \
    --user-data-dir="${CHROME_PROFILE}" \
    --no-first-run \
    --no-default-browser-check \
    --window-size=1280,900 \
    --window-position=100,50 \
    "${URL}" >/dev/null 2>&1 &
}

case "${URL}" in
  grokbot:*|sand:*)
    exec env DISPLAY="${DISPLAY}" "${GROK_BIN}" \
      --user-data-dir="${GC2_DATA_DIR}" \
      "${URL}"
    ;;
  http:*|https:*)
    chrome_window
    exit 0
    ;;
  *)
    exec env DISPLAY="${DISPLAY}" MOZ_DISABLE_REMOTE=1 /usr/bin/xdg-open "${URL}"
    ;;
esac
