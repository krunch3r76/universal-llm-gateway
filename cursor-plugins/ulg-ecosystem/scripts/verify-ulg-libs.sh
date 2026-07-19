#!/usr/bin/env bash
# sessionStart verify — warn if ULG libs/ is unreachable.
# Primary PYTHONPATH guarantee remains $HOME/.venvs/universal sitecustomize.py
# (injects ULG root + libs/). This hook is a safety net, not the injector.

set -euo pipefail

ULG_ROOT="${ULG_REPO:-}"
if [[ -z "$ULG_ROOT" && -f "${HOME}/.gateway/repo_path" ]]; then
  ULG_ROOT="$(tr -d '\r\n' < "${HOME}/.gateway/repo_path")"
fi
if [[ -z "$ULG_ROOT" ]]; then
  ULG_ROOT="/mnt/torus/projects/universal-llm-gateway"
fi

PYTHON_BIN="${ULG_PYTHON:-${HOME}/.venvs/universal/bin/python}"
if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="$(command -v python3 || true)"
fi

if [[ -z "$PYTHON_BIN" ]]; then
  echo "[ulg-ecosystem] WARN: no python interpreter found; cannot verify ULG libs/" >&2
  exit 0
fi

if ! "$PYTHON_BIN" -c "import transport_utils" 2>/dev/null; then
  echo "[ulg-ecosystem] WARN: transport_utils not importable." >&2
  echo "[ulg-ecosystem] Expect \$HOME/.venvs/universal (sitecustomize injects ${ULG_ROOT}/libs)." >&2
  echo "[ulg-ecosystem] Or: export PYTHONPATH=\"${ULG_ROOT}/libs:\${PYTHONPATH}\"" >&2
  exit 0
fi

echo "[ulg-ecosystem] OK: ULG libs/ reachable (transport_utils importable)"
exit 0
