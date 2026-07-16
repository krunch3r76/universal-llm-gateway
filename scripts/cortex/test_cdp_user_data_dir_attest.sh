#!/usr/bin/env bash
# Falsifier for the ensure_chrome early-exit user-data-dir attest (T1 / 24612
# residual). Mirrors the process-scan + parse + canonicalize + compare snippet
# in scripts/cortex/claude-ai-sync-jupiter ensure_chrome. If that snippet
# changes, update this contract in lockstep.
#
# Contract:
#   match   (observed --user-data-dir canonically == expected PROFILE) -> exit 0
#   mismatch                                                            -> exit 3
#   no owning process / no --user-data-dir                              -> exit 3
#
# Runs fully locally with a mock /proc — never touches Jupiter or :9222.

set -uo pipefail

WORK="$(mktemp -d)"
trap 'rm -rf "${WORK}"' EXIT

PROC="${WORK}/proc"
mkdir -p "${PROC}"

# Write a mock /proc/<pid>/cmdline (NUL-separated) from separate args.
mock_proc() {
  local pid="$1"; shift
  mkdir -p "${PROC}/${pid}"
  printf '%s\0' "$@" > "${PROC}/${pid}/cmdline"
}

# Chrome's MAIN process rewrites argv to a single SPACE-joined string (no NULs).
# This is the real-world format that broke the first NUL-only implementation.
mock_proc_spacejoined() {
  local pid="$1"; shift
  mkdir -p "${PROC}/${pid}"
  printf '%s' "$*" > "${PROC}/${pid}/cmdline"
}

# Mock pgrep -f "remote-debugging-port=PORT": echo pids whose cmdline contains it.
mock_pgrep_pids() {
  local needle="$1" pid
  for pid in "${PROC}"/*/; do
    [[ -d "${pid}" ]] || continue
    pid="$(basename "${pid}")"
    if tr '\0' '\n' < "${PROC}/${pid}/cmdline" 2>/dev/null | grep -qF -- "${needle}"; then
      echo "${pid}"
    fi
  done
}

# Attest — identical logic to the heredoc, reading the mock /proc.
attest() {
  local PORT="$1" PROFILE="$2"
  local OBSERVED_DIR="" pid cmd
  for pid in $(mock_pgrep_pids "remote-debugging-port=${PORT}"); do
    [[ -r "${PROC}/${pid}/cmdline" ]] || continue
    cmd="$(tr '\0' ' ' < "${PROC}/${pid}/cmdline")"
    if grep -qP -- "--remote-debugging-port=${PORT}(?![0-9])" <<<"${cmd}"; then
      OBSERVED_DIR="$(grep -oP -- '--user-data-dir=\K\S+' <<<"${cmd}" | head -n1)"
      [[ -n "${OBSERVED_DIR}" ]] && break
    fi
  done
  if [[ -z "${OBSERVED_DIR}" ]]; then
    echo "no owning process" >&2
    return 3
  fi
  local EXP_CANON OBS_CANON
  EXP_CANON="$(realpath -m "${PROFILE}")"
  OBS_CANON="$(realpath -m "${OBSERVED_DIR}")"
  if [[ "${EXP_CANON}" != "${OBS_CANON}" ]]; then
    echo "mismatch expected=${EXP_CANON} observed=${OBS_CANON}" >&2
    return 3
  fi
  echo "attested ${OBS_CANON}"
  return 0
}

fail=0
check() {
  local name="$1" want="$2" got="$3"
  if [[ "${want}" == "${got}" ]]; then
    echo "PASS ${name} (exit ${got})"
  else
    echo "FAIL ${name}: want exit ${want}, got ${got}" >&2
    fail=1
  fi
}

HOME_DIR="/home/tester"
PRIMARY="${HOME_DIR}/.gateway/claude-ai-chrome-profile"

# 1. Match on :9222 primary (empty suffix) -> exit 0
rm -rf "${PROC:?}"/*; mkdir -p "${PROC}"
mock_proc 100 google-chrome "--remote-debugging-port=9222" "--user-data-dir=${PRIMARY}" --no-first-run
attest 9222 "${PRIMARY}" >/dev/null 2>&1; check "9222 primary match" 0 $?

# 2. Match with trailing-slash observed dir -> canonicalized equal -> exit 0
rm -rf "${PROC:?}"/*; mkdir -p "${PROC}"
mock_proc 101 google-chrome "--remote-debugging-port=9223" "--user-data-dir=${PRIMARY}-ask/"
attest 9223 "${PRIMARY}-ask" >/dev/null 2>&1; check "trailing-slash canonical match" 0 $?

# 3. MISMATCH: :9224 owned by fable-consult, caller expects -ask -> exit 3
rm -rf "${PROC:?}"/*; mkdir -p "${PROC}"
mock_proc 102 google-chrome "--remote-debugging-port=9224" "--user-data-dir=${PRIMARY}-fable-consult"
attest 9224 "${PRIMARY}-ask" >/dev/null 2>&1; check "cross-lane mismatch fails closed" 3 $?

# 4. No owning process for the port -> exit 3
rm -rf "${PROC:?}"/*; mkdir -p "${PROC}"
mock_proc 103 google-chrome "--remote-debugging-port=9299" "--user-data-dir=${PRIMARY}"
attest 9223 "${PRIMARY}-ask" >/dev/null 2>&1; check "no owning process fails closed" 3 $?

# 5. Process on port but no --user-data-dir arg -> exit 3
rm -rf "${PROC:?}"/*; mkdir -p "${PROC}"
mock_proc 104 google-chrome "--remote-debugging-port=9223" --no-first-run
attest 9223 "${PRIMARY}-ask" >/dev/null 2>&1; check "missing user-data-dir fails closed" 3 $?

# 6. Exact port token — 9222 must NOT match a 92220 process -> no owner -> exit 3
rm -rf "${PROC:?}"/*; mkdir -p "${PROC}"
mock_proc 105 google-chrome "--remote-debugging-port=92220" "--user-data-dir=${PRIMARY}"
attest 9222 "${PRIMARY}" >/dev/null 2>&1; check "no substring port match" 3 $?

# 7. Real-world: SPACE-joined main-process cmdline (Chrome rewrites argv) -> match
rm -rf "${PROC:?}"/*; mkdir -p "${PROC}"
mock_proc_spacejoined 106 /opt/google/chrome/chrome "--remote-debugging-port=9223" "--remote-allow-origins=*" "--user-data-dir=${PRIMARY}-ask" --no-first-run
attest 9223 "${PRIMARY}-ask" >/dev/null 2>&1; check "space-joined cmdline match" 0 $?

# 8. Space-joined main-process cmdline, cross-lane mismatch -> exit 3
rm -rf "${PROC:?}"/*; mkdir -p "${PROC}"
mock_proc_spacejoined 107 /opt/google/chrome/chrome "--remote-debugging-port=9224" "--user-data-dir=${PRIMARY}-fable-consult" --no-first-run
attest 9224 "${PRIMARY}-ask" >/dev/null 2>&1; check "space-joined cross-lane mismatch" 3 $?

exit "${fail}"
