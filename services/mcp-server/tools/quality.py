"""Quality gate tools — ruff lint + compileall for code validation.

Allows agents to verify code quality before committing changes.
Runs against files in the project directory mounted at /data/project.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mcp_events import monotonic_now, record

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(os.environ.get("PROJECT_ROOT", "/data/project"))
_TIMEOUT = 30


def register_quality_tools(mcp: FastMCP) -> None:
    """Register code quality gate tools."""

    @mcp.tool(title="Quality Gate")
    def quality_gate(files: list[str]) -> dict[str, Any]:
        """Run ruff lint + compileall on specified files. Returns {passed, ruff, compile}."""
        t0 = monotonic_now()
        record("mcp.quality.gate.called", file_count=len(files))

        existing = _resolve_existing_files(files)
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


def _resolve_existing_files(files: list[str]) -> list[str]:
    """Resolve agent-supplied file paths inside the mounted project root."""
    root = _PROJECT_ROOT.resolve()
    existing: list[str] = []
    for file in files:
        for candidate in _candidate_paths(file, root):
            if candidate.exists():
                existing.append(str(candidate))
                break
    return existing


def _candidate_paths(file: str, root: Path) -> list[Path]:
    candidates: list[Path] = []
    seen: set[Path] = set()
    input_path = Path(file)
    repos = _repo_roots(root)

    def add(candidate: Path) -> None:
        normalized = candidate.resolve() if candidate.exists() else candidate
        if normalized not in seen:
            seen.add(normalized)
            candidates.append(normalized)

    if input_path.is_absolute():
        add(input_path)
    else:
        add(root / file)

    parts = _path_parts_without_anchor(input_path)
    if parts and parts[0] == root.name:
        add(root.joinpath(*parts[1:]))
    for repo in repos:
        if input_path.is_absolute():
            _add_path_from_named_prefix(parts, repo, add)
        else:
            add(repo / file)
            if parts and parts[0] == repo.name:
                add(repo.joinpath(*parts[1:]))

    return candidates


def _repo_roots(root: Path) -> list[Path]:
    if (root / ".git").exists():
        return [root]
    try:
        children = [child for child in sorted(root.iterdir()) if child.is_dir()]
    except FileNotFoundError:
        return [root]
    repos = [child for child in children if (child / ".git").exists()]
    if not repos:
        repos = [child for child in children if not child.name.startswith(".")]
    return repos or [root]


def _path_parts_without_anchor(path: Path) -> list[str]:
    return [part for part in path.parts if part not in {path.anchor, ""}]


def _add_path_from_named_prefix(
    parts: list[str],
    repo: Path,
    add: Callable[[Path], None],
) -> None:
    if repo.name not in parts:
        return
    repo_name_index = parts.index(repo.name)
    add(repo.joinpath(*parts[repo_name_index + 1 :]))


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
