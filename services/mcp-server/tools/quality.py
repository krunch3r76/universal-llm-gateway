"""Quality gate tools — ruff lint, compileall, and import resolution.

Allows agents to verify code quality before committing changes.
Runs against files in the project directory mounted at /data/project.

compileall validates syntax only; import checking executes importlib so
broken relative imports (e.g. package-shadow splits) fail before restart.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mcp_events import monotonic_now, record

from ._project_paths import resolve_existing_files

if TYPE_CHECKING:
    from fastmcp import FastMCP

_PROJECT_ROOT = Path(os.environ.get("PROJECT_ROOT", "/data/project"))
_TIMEOUT = 30
_TEST_TIMEOUT = 120
_OFFLINE_CLOSURE = ("/libs/llm_adapters/", "/libs/model_id/")
_OFFLINE_TEST_PATHS = ("libs/llm_adapters", "libs/model_id")


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
        tests_result = _run_offline_tests(existing)

        passed = (
            ruff_result["passed"]
            and compile_result["passed"]
            and import_result["passed"]
            and tests_result["passed"]
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
            "tests": tests_result,
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
            [sys.executable, "-m", "compileall", "-q", *files],
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


def _offline_repo_root(files: list[str]) -> Path | None:
    for path in files:
        for segment in _OFFLINE_CLOSURE:
            if segment in path:
                return Path(path.split(segment, 1)[0])
    return None


def _run_offline_tests(
    files: list[str], *, run_tests: bool = True
) -> dict[str, bool | str]:
    if not run_tests or not any(
        any(seg in f for seg in _OFFLINE_CLOSURE) for f in files
    ):
        return {"passed": True, "output": "no offline-closure files touched; skipped"}
    repo_root = _offline_repo_root(files)
    if repo_root is None:
        return {"passed": True, "output": "no offline-closure files touched; skipped"}
    try:
        probe = subprocess.run(
            [sys.executable, "-c", "import pytest"],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return {
            "passed": False,
            "output": "pytest probe failed; offline tests unavailable",
        }
    if probe.returncode != 0:
        return {
            "passed": False,
            "output": "pytest unavailable in image; offline tests blocked",
        }
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo_root / "libs")
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-m",
                "offline",
                "-q",
                "--no-header",
                "-p",
                "no:cacheprovider",
                *_OFFLINE_TEST_PATHS,
            ],
            capture_output=True,
            text=True,
            timeout=_TEST_TIMEOUT,
            cwd=str(repo_root),
            env=env,
        )
        out = (result.stdout + result.stderr).strip()
        return {
            "passed": result.returncode == 0,
            "output": out[:4000] if out else "(no output)",
        }
    except FileNotFoundError:
        return {"passed": False, "output": "python executable not found in PATH"}
    except subprocess.TimeoutExpired:
        return {"passed": False, "output": "offline tests timed out"}
