"""Quality gate tools — ruff lint, compileall, and import resolution.

Allows agents to verify code quality before committing changes.
Runs against files in the project directory mounted at /data/project.

compileall validates syntax only; import checking executes importlib so
broken relative imports (e.g. package-shadow splits) fail before restart.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mcp_events import monotonic_now, record

from ._project_paths import resolve_existing_files

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(os.environ.get("PROJECT_ROOT", "/data/project"))
_TIMEOUT = 30


def register_quality_tools(mcp: FastMCP) -> None:
    """Register code quality gate tools."""

    @mcp.tool(title="Quality Gate")
    def quality_gate(files: list[str]) -> dict[str, Any]:
        """Run ruff lint, compileall, and import checks on specified files."""
        t0 = monotonic_now()
        record("mcp.quality.gate.called", file_count=len(files))

        existing = _resolve_existing_files(files)
        if not existing:
            return {"passed": False, "error": "No valid files found"}

        ruff_result = _run_ruff(existing)
        compile_result = _run_compileall(existing)
        import_result = _run_import_check(existing)

        passed = (
            ruff_result["passed"]
            and compile_result["passed"]
            and import_result["passed"]
        )
        duration = monotonic_now() - t0

        record(
            "mcp.quality.gate.completed",
            passed=passed,
            duration_s=round(duration, 3),
        )

        return {
            "passed": passed,
            "ruff": ruff_result,
            "compile": compile_result,
            "imports": import_result,
        }


def _resolve_existing_files(files: list[str]) -> list[str]:
    return resolve_existing_files(files, root=_PROJECT_ROOT.resolve())


def _run_ruff(files: list[str]) -> dict[str, bool | str]:
    """Run ruff check on files."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "ruff", "check", *files],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT,
        )
        return {
            "passed": result.returncode == 0,
            "output": result.stdout[:2000] if result.stdout else result.stderr[:2000],
        }
    except FileNotFoundError:
        return {"passed": False, "output": "python executable not found in PATH"}
    except subprocess.TimeoutExpired:
        return {"passed": False, "output": "ruff timed out"}


def _run_compileall(files: list[str]) -> dict[str, bool | str]:
    """Run compileall on files."""
    try:
        result = subprocess.run(
            ["python", "-m", "compileall", "-q", *files],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT,
        )
        return {
            "passed": result.returncode == 0,
            "output": result.stdout[:2000] if result.stdout else result.stderr[:2000],
        }
    except FileNotFoundError:
        return {"passed": False, "output": "python executable not found in PATH"}
    except subprocess.TimeoutExpired:
        return {"passed": False, "output": "compileall timed out"}


def _run_import_check(files: list[str]) -> dict[str, bool | str]:
    """Execute scripts/check-imports on Python files (runtime import resolution)."""
    py_files = [path for path in files if path.endswith(".py")]
    if not py_files:
        return {"passed": True, "output": "no Python files to import-check"}

    check_script = _PROJECT_ROOT / "scripts" / "check-imports"
    if not check_script.exists():
        return {
            "passed": False,
            "output": f"check-imports script missing: {check_script}",
        }

    stargate_root = (_PROJECT_ROOT / "services" / "universal-stargate").resolve()
    needs_stargate_entry = any(
        Path(path).resolve().is_relative_to(stargate_root) for path in py_files
    )

    cmd = [sys.executable, str(check_script)]
    if needs_stargate_entry:
        cmd.append("--stargate-entry")
    cmd.extend(py_files)

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=max(_TIMEOUT, 60),
            cwd=str(_PROJECT_ROOT),
        )
        output = (result.stdout + result.stderr).strip()
        return {
            "passed": result.returncode == 0,
            "output": output[:4000] if output else "(no output)",
        }
    except subprocess.TimeoutExpired:
        return {"passed": False, "output": "check-imports timed out"}
