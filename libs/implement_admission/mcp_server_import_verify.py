"""Import smoke for ``services/mcp-server/`` paths at propagation mint.

``rows_from_service_paths`` previously stamped ``import_path:not_probed`` for
every path-prefix row, including mcp — so a broken tool import shipped until
server boot. This module probes changed mcp-server Python modules (and
``server`` bootstrap when needed) with the same ``PYTHONPATH`` the process
uses: ``libs`` + ``services/mcp-server``.

Callers: ``propagation_row.rows_from_service_paths``.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from implement_admission.consumer_import_verify import ImportPathStatus, repo_root

_MCP_PREFIX = "services/mcp-server/"


def _normalize_path(path: str) -> str:
    return str(path or "").replace("\\", "/")


def is_mcp_server_path(path: str) -> bool:
    """True when *path* is under ``services/mcp-server/``."""
    return _normalize_path(path).startswith(_MCP_PREFIX)


def is_mcp_runtime_module(path: str) -> bool:
    """True for importable runtime Python under mcp-server (¬ tests/conftest)."""
    text = _normalize_path(path)
    if not text.startswith(_MCP_PREFIX) or not text.endswith(".py"):
        return False
    if "/tests/" in text or text.endswith("/conftest.py"):
        return False
    basename = text.rsplit("/", 1)[-1]
    if basename.startswith("test_") or basename.endswith("_test.py"):
        return False
    return True


def module_for_mcp_path(path: str) -> str | None:
    """Map a repo-relative mcp-server ``.py`` path to an importable module name."""
    text = _normalize_path(path)
    if not is_mcp_runtime_module(text):
        return None
    rel = text[len(_MCP_PREFIX) : -3]
    if rel.endswith("/__init__"):
        rel = rel[: -len("/__init__")]
    if not rel:
        return None
    return rel.replace("/", ".")


def modules_to_probe(paths: list[str]) -> tuple[str, ...]:
    """Deduped import targets for touched mcp-server paths."""
    seen: set[str] = set()
    ordered: list[str] = []
    for path in paths:
        module = module_for_mcp_path(path)
        if module is None or module in seen:
            continue
        seen.add(module)
        ordered.append(module)
    if not ordered and any(is_mcp_server_path(p) for p in paths):
        return ("server",)
    return tuple(ordered)


def _run_import(module: str, *, mcp_dir: str, libs_dir: str) -> bool:
    """Return True when *module* imports cleanly under mcp-server PYTHONPATH."""
    env = os.environ.copy()
    env["PYTHONPATH"] = f"{libs_dir}{os.pathsep}{mcp_dir}"
    result = subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        env=env,
        capture_output=True,
        timeout=30,
        check=False,
    )
    return result.returncode == 0


def mcp_server_import_smoke(
    paths: list[str],
    *,
    root: Path | None = None,
) -> ImportPathStatus:
    """Probe import closure for touched mcp-server paths.

    Returns ``not_probed`` when no mcp-server path is present. Any failed
    import among probed modules yields ``contradicted``; all succeed →
    ``verified``. Subprocess infrastructure failure → ``indeterminate``.
    """
    if not any(is_mcp_server_path(p) for p in paths):
        return "not_probed"

    base = root if root is not None else repo_root()
    mcp_dir = str(base / "services" / "mcp-server")
    libs_dir = str(base / "libs")
    modules = modules_to_probe(paths)
    if not modules:
        return "indeterminate"

    for module in modules:
        try:
            ok = _run_import(module, mcp_dir=mcp_dir, libs_dir=libs_dir)
        except (OSError, subprocess.TimeoutExpired):
            return "indeterminate"
        if not ok:
            return "contradicted"
    return "verified"


__all__ = [
    "is_mcp_runtime_module",
    "is_mcp_server_path",
    "mcp_server_import_smoke",
    "module_for_mcp_path",
    "modules_to_probe",
]
