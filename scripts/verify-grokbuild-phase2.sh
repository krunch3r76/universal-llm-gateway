#!/usr/bin/env bash
# Phase-2 verification script for grokbuild dispatch tool.
#
# Closes phase-2-tests.md §Verification:
#   - all 24 §5.11 tests pass (now 25 with the added staged-violation test
#     plus parametrized expansions on #11 / #20)
#   - no real subprocess spawn (mock-call accounting)
#   - coverage ≥90% on Option D runner paths
#   - ruff + compileall clean
#   - mutation-test smoke: predicate-incomplete mutants fail at least one test
#
# Usage: bash scripts/verify-grokbuild-phase2.sh
# Exit: 0 on full pass; non-zero on first failure.

set -euo pipefail

REPO_ROOT="${REPO_ROOT:-/mnt/torus/projects/universal-llm-gateway}"
VENV="${VENV:-$HOME/.venvs/universal}"
TOOLS_DIR="$REPO_ROOT/services/mcp-server/tools"
PYTHON="$VENV/bin/python"
PYTEST="$VENV/bin/pytest"
PIP="$VENV/bin/pip"
RUFF="$VENV/bin/ruff"

if [[ ! -x "$PYTHON" ]]; then
  echo "FAIL: venv not found at $VENV — adjust VENV env var" >&2
  exit 2
fi

cd "$REPO_ROOT"

echo "=== Step 1: pytest-cov install (idempotent) ==="
"$PIP" install --quiet pytest-cov >/dev/null || {
  echo "FAIL: pytest-cov install failed" >&2
  exit 3
}

echo "=== Step 2: ruff lint ==="
echo "  ruff version: $("$RUFF" --version)"
"$RUFF" check "$TOOLS_DIR"/_grokbuild_*.py "$TOOLS_DIR"/grokbuild.py "$TOOLS_DIR"/test_grokbuild_*.py || {
  echo "FAIL: ruff lint failed" >&2
  exit 4
}
if ! "$RUFF" format --check "$TOOLS_DIR"/_grokbuild_*.py "$TOOLS_DIR"/grokbuild.py "$TOOLS_DIR"/test_grokbuild_*.py; then
  echo "--- ruff format --diff (showing required changes) ---" >&2
  "$RUFF" format --diff "$TOOLS_DIR"/_grokbuild_*.py "$TOOLS_DIR"/grokbuild.py "$TOOLS_DIR"/test_grokbuild_*.py >&2 || true
  echo "FAIL: ruff format check failed" >&2
  exit 5
fi

echo "=== Step 3: compileall ==="
"$PYTHON" -m compileall -q "$TOOLS_DIR"/_grokbuild_*.py "$TOOLS_DIR"/grokbuild.py "$TOOLS_DIR"/test_grokbuild_*.py || {
  echo "FAIL: compileall failed" >&2
  exit 6
}

echo "=== Step 4: pytest with coverage ==="
cd "$REPO_ROOT/services/mcp-server"
"$PYTEST" \
  tools/test_grokbuild_handler.py \
  tools/test_grokbuild_runner.py \
  tools/test_grokbuild_validator.py \
  -v \
  --cov=tools.grokbuild \
  --cov=tools._grokbuild_runner \
  --cov=tools._grokbuild_validator \
  --cov=tools._grokbuild_events \
  --cov-report=term-missing \
  --cov-fail-under=90 \
  --tb=short || {
  echo "FAIL: pytest or coverage gate failed" >&2
  exit 7
}

echo "=== Step 5: mutation-test smoke on _read_only_violation ==="
# Verifies that the test suite would catch the staged-only bug we just fixed
# AND a flat inversion. If either mutant passes all tests, the suite is too
# permissive on this predicate.
PREDICATE_FILE="$TOOLS_DIR/grokbuild.py"
BACKUP="$(mktemp)"
cp "$PREDICATE_FILE" "$BACKUP"
restore_predicate() { cp "$BACKUP" "$PREDICATE_FILE"; }
trap restore_predicate EXIT

cd "$REPO_ROOT/services/mcp-server"

# Mutant A: predicate always False (audit always passes)
"$PYTHON" - <<'PY'
import pathlib
p = pathlib.Path("tools/grokbuild.py")
src = p.read_text()
target = (
    '    if mode != "read_only":\n'
    '        return False\n'
    '    return bool(git_diff_stat.strip()) or bool(git_status_post.strip())'
)
replacement = (
    '    if mode != "read_only":\n'
    '        return False\n'
    '    return False  # MUTANT A'
)
assert target in src, "Mutant A target string not found — predicate shape may have changed"
p.write_text(src.replace(target, replacement))
PY
if "$PYTEST" tools/test_grokbuild_handler.py -q --tb=no >/dev/null 2>&1; then
  echo "FAIL: mutation-test A — predicate-always-False passed all tests; suite cannot detect a flat false-negative" >&2
  restore_predicate
  exit 8
fi
echo "  Mutant A (always-False): correctly detected"

# Mutant B: predicate drops the git_status_post.strip() branch (regression to old bug)
restore_predicate
"$PYTHON" - <<'PY'
import pathlib
p = pathlib.Path("tools/grokbuild.py")
src = p.read_text()
patched = src.replace(
    "    return bool(git_diff_stat.strip()) or bool(git_status_post.strip())",
    "    return bool(git_diff_stat.strip()) or any(line.startswith(\"??\") for line in git_status_post.splitlines())  # MUTANT B (regression)",
)
assert patched != src, "Mutant B patch did not match source"
p.write_text(patched)
PY
if "$PYTEST" tools/test_grokbuild_handler.py::test_read_only_staged_modification_violation -q --tb=no >/dev/null 2>&1; then
  echo "FAIL: mutation-test B — staged-mutation regression passed; the new staged-mutation test does not actually guard the fix" >&2
  restore_predicate
  exit 9
fi
echo "  Mutant B (staged-regression): correctly detected"

restore_predicate
trap - EXIT

echo
echo "=== ALL CHECKS PASSED ==="
echo "  ruff:               clean"
echo "  compileall:         clean"
echo "  pytest:             25+ tests pass"
echo "  coverage:           ≥90% on Option D runner paths"
echo "  mutation-test A:    flat false-negative detected"
echo "  mutation-test B:    staged-regression detected"
