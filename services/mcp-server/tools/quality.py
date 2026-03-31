"""Quality gate tools — ruff lint + compileall for code validation.

Allows agents to verify code quality before committing changes.
Runs against files in the project directory mounted at /data/project.
"""

from __future__ import annotations

import logging
import os
import subprocess
from typing import TYPE_CHECKING, Any

from mcp_events import monotonic_now, record

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = logging.getLogger(__name__)

_PROJECT_ROOT = os.environ.get("PROJECT_ROOT", "/data/project")
_TIMEOUT = 30


def register_quality_tools(mcp: FastMCP) -> None:
    """Register code quality gate tools."""

    @mcp.tool()
    def quality_gate(files: list[str]) -> dict[str, Any]:
        """Run ruff lint + compileall on specified files. Returns {passed, ruff, compile}."""
        t0 = monotonic_now()
        record("mcp.quality.gate.called", file_count=len(files))

        abs_files = [os.path.join(_PROJECT_ROOT, f) for f in files]

        existing = [f for f in abs_files if os.path.exists(f)]
        if not existing:
            return {"passed": False, "error": "No valid files found"}

        ruff_result = _run_ruff(existing)
        compile_result = _run_compileall(existing)

        passed = ruff_result["passed"] and compile_result["passed"]
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
        }


def _run_ruff(files: list[str]) -> dict[str, bool | str]:
    """Run ruff check on files."""
    try:
        result = subprocess.run(
            ["ruff", "check", *files],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT,
        )
        return {
            "passed": result.returncode == 0,
            "output": result.stdout[:2000] if result.stdout else result.stderr[:2000],
        }
    except FileNotFoundError:
        return {"passed": False, "output": "ruff not found in PATH"}
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
