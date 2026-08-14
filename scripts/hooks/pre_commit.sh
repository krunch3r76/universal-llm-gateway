#!/usr/bin/env bash
# Catalog + agent-surface pre-commit gate — installed via install-hooks.sh.
#
# Automatic trigger (AC6): runs on every ``git commit`` attempt without
# ``--no-verify``. OpenAPI fleet gate delegates to scripts/agent-surface-check.

set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=resolve_hook_python.sh
source "$SCRIPT_DIR/resolve_hook_python.sh"

PYTHON="$(resolve_hook_python || true)"
if [[ -z "$PYTHON" || ! -x "$PYTHON" ]]; then
    echo "FATAL: pre-commit hook: no executable python3 (tried VIRTUAL_ENV, HOME/.venvs/universal, PATH)" >&2
    exit 1
fi

"$PYTHON" scripts/hooks/validate_catalog.py --staged || exit 1
"$PYTHON" scripts/hooks/validate_skill_catalog_staged.py || exit 1
"$PYTHON" -m scripts.gen_event_catalog sync --staged || exit 1
git add docs/event-contracts.md 2>/dev/null || true
"$PYTHON" -m scripts.gen_event_catalog check --staged || exit 1
"$PYTHON" scripts/check-rag-events-imports.py --staged || exit 1
"$PYTHON" scripts/lint-fastapi-annotations.py --staged || exit 1
bash scripts/agent-surface-check --openapi-only || exit 1
"$PYTHON" scripts/cortex/run_skill_git_guard.py || exit 1
