"""GIW process PATH — venv bin first, independent of stale manage spawn-env.

``_runtime_env`` runs inside the long-lived ``./manage`` process. A GIW
``sync_restart`` reloads worker code, not the controller module that built
the spawn env (arc 7190). This module corrects ``os.environ["PATH"]`` in
the worker itself so ruff/pytest resolve to the pin even when manage is
still running a pre-fix ``_runtime_env``.

``/proc/<pid>/environ`` stays the exec-time snapshot and will not reflect
this correction. Probe ``os.environ`` / ``GET /health`` ``path_first``.
"""

from __future__ import annotations

import os
from collections.abc import MutableMapping
from dataclasses import dataclass
from pathlib import Path


def venv_bin_dir() -> str:
    """Resolve the universal venv bindir from the process home, not $PATH."""
    return str(Path.home() / ".venvs" / "universal" / "bin")


def path_with_venv_first(path: str, venv_bin: str) -> str:
    """Return PATH with *venv_bin* first, even when it already appears later."""
    parts = [p for p in path.split(":") if p and p != venv_bin]
    return f"{venv_bin}:{':'.join(parts)}" if parts else venv_bin


@dataclass(frozen=True, slots=True)
class ToolchainPathReport:
    """Spawn PATH vs the PATH the worker actually uses after correction."""

    spawn_path: str
    effective_path: str
    spawn_first: str
    effective_first: str
    corrected: bool


def apply_toolchain_path(
    environ: MutableMapping[str, str] | None = None,
) -> ToolchainPathReport:
    """Put the venv bin first on *environ* (default ``os.environ``).

    Returns a report comparing the spawn PATH to the effective PATH so a
    restart probe can distinguish ancestry (code_version) from inheritance.
    """
    env = os.environ if environ is None else environ
    spawn = env.get("PATH", "/usr/bin:/bin")
    venv = venv_bin_dir()
    effective = path_with_venv_first(spawn, venv)
    env["PATH"] = effective
    spawn_first = spawn.split(":", 1)[0] if spawn else ""
    effective_first = effective.split(":", 1)[0] if effective else ""
    return ToolchainPathReport(
        spawn_path=spawn,
        effective_path=effective,
        spawn_first=spawn_first,
        effective_first=effective_first,
        corrected=effective != spawn,
    )


__all__ = [
    "ToolchainPathReport",
    "apply_toolchain_path",
    "path_with_venv_first",
    "venv_bin_dir",
]
