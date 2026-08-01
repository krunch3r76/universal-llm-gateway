#!/usr/bin/env bash
# Fail-closed python discovery for git hooks.
#
# Dispatch seats swap HOME away from the operator home; the universal venv
# usually remains first on PATH. A hook must not fail open when its
# interpreter is missing.

resolve_hook_python() {
    local candidate=""

    if [[ -n "${VIRTUAL_ENV:-}" ]]; then
        candidate="${VIRTUAL_ENV}/bin/python3"
        if [[ -x "$candidate" ]]; then
            printf '%s\n' "$candidate"
            return 0
        fi
    fi

    candidate="$(command -v python3 2>/dev/null || true)"
    if [[ -n "$candidate" && -x "$candidate" ]]; then
        printf '%s\n' "$candidate"
        return 0
    fi

    candidate="${HOME}/.venvs/universal/bin/python3"
    if [[ -x "$candidate" ]]; then
        printf '%s\n' "$candidate"
        return 0
    fi

    return 1
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    if ! resolve_hook_python; then
        echo "FATAL: pre-commit hook: no executable python3 (tried VIRTUAL_ENV, PATH, HOME/.venvs/universal)" >&2
        exit 1
    fi
fi
