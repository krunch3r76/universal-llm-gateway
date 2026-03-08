"""Project directory tools — read-only access to the mounted project.

All paths are resolved relative to _PROJECT_ROOT. Traversal attempts
(../) are rejected. The volume mount is :ro but code-level enforcement
provides defense in depth — no write functions exist.

Excluded from listing: .git, __pycache__, node_modules, .venv, *.pyc, etc.
Binary files are rejected from reading.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(os.environ.get("PROJECT_ROOT", "/data/project"))

_EXCLUDED_DIRS = {
    ".git",
    "__pycache__",
    "node_modules",
    ".venv",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
    "egg-info",
}

_BINARY_SUFFIXES = {
    ".pyc", ".pyo", ".so", ".dll", ".dylib", ".o", ".a",
    ".whl", ".egg", ".gz", ".tar", ".zip", ".bz2", ".xz",
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp", ".svg",
    ".mp3", ".mp4", ".wav", ".avi", ".mov",
    ".bin", ".dat", ".db", ".sqlite", ".sqlite3",
    ".pkl", ".pickle", ".npy", ".npz",
    ".gguf", ".ggml", ".safetensors",
    ".ttf", ".otf", ".woff", ".woff2",
    ".pdf",
}


def _safe_project_path(relative: str) -> Path:
    """Resolve *relative* inside the project root, rejecting traversal."""
    clean = relative.lstrip("/")
    resolved_project_root = _PROJECT_ROOT.resolve()
    candidate = (resolved_project_root / clean).resolve()
    try:
        candidate.relative_to(resolved_project_root)
    except ValueError:
        raise ValueError(
            f"Path {relative!r} resolves outside project root; traversal rejected"
        )
    return candidate


def _is_excluded_dir(path: Path) -> bool:
    """Return True if any component of *path* is in the exclusion set."""
    def _excluded(part: str) -> bool:
        return part in _EXCLUDED_DIRS or part.endswith(".egg-info")

    return any(_excluded(part) for part in path.parts)


def _is_binary(path: Path) -> bool:
    """Return True if the file's suffix indicates binary content."""
    return path.suffix.lower() in _BINARY_SUFFIXES


def register_project_tools(mcp: FastMCP) -> None:
    """Register read-only project directory tools on *mcp*."""

    @mcp.tool()
    def read_project_file(path: str) -> dict[str, str]:
        """Read a file from the project directory (read-only).

        Only text files are supported. Binary files (.pyc, .so, images,
        model weights, etc.) are rejected.

        Args:
            path: Relative file path within the project, e.g. "services/mcp-server/server.py".

        Returns:
            {"content": "<file contents>", "path": "<relative path>"}
        """
        src = _safe_project_path(path)
        if not src.exists():
            raise FileNotFoundError(f"File not found: {path!r}")
        if not src.is_file():
            raise ValueError(f"Path is not a file: {path!r}")
        if _is_binary(src):
            raise ValueError(
                f"Binary file type {src.suffix!r} cannot be read. "
                "Only text files are supported."
            )

        content = src.read_text(encoding="utf-8", errors="replace")
        rel = str(src.relative_to(_PROJECT_ROOT.resolve()))
        logger.info("read_project_file: %s (%d chars)", rel, len(content))
        return {"content": content, "path": rel}

    @mcp.tool()
    def list_project_files(
        directory: str = "",
        max_depth: int = 3,
    ) -> dict[str, list[str] | bool]:
        """List files in the project directory with depth limiting.

        Excludes .git, __pycache__, node_modules, .venv, and binary files.
        Use max_depth to control how deep the listing goes (default 3).

        Args:
            directory: Relative directory path. Empty string lists the project root.
            max_depth: Maximum directory depth to recurse (1 = immediate children only).

        Returns:
            {"files": ["<relative paths>", ...], "truncated": false}
        """
        target = _safe_project_path(directory) if directory else _PROJECT_ROOT
        if not target.exists():
            return {"files": [], "truncated": False}
        if not target.is_dir():
            raise ValueError(f"Path is not a directory: {directory!r}")

        resolved_root = _PROJECT_ROOT.resolve()
        resolved_target = target.resolve()
        base_depth = len(resolved_target.relative_to(resolved_root).parts)

        files: list[str] = []
        cap = 2000

        for item in sorted(resolved_target.rglob("*")):
            rel = item.relative_to(resolved_root)
            depth = len(rel.parts) - base_depth
            if depth > max_depth:
                continue
            if _is_excluded_dir(rel):
                continue
            if item.is_file() and not _is_binary(item):
                files.append(str(rel))
                if len(files) >= cap:
                    break

        truncated = len(files) >= cap
        logger.info(
            "list_project_files: %s depth=%d → %d files%s",
            directory or "/",
            max_depth,
            len(files),
            " (truncated)" if truncated else "",
        )
        return {"files": files, "truncated": truncated}

    @mcp.tool()
    def search_project_files(
        pattern: str,
        directory: str = "",
        max_results: int = 50,
    ) -> dict[str, list[dict[str, str | int]] | bool]:
        """Search for a regex pattern across project text files.

        Searches line-by-line through text files, skipping binary files
        and excluded directories.

        Args:
            pattern: Regex pattern to search for (case-sensitive).
            directory: Relative directory to search within. Empty = project root.
            max_results: Maximum number of matching lines to return (default 50).

        Returns:
            {"matches": [{"file": "...", "line": N, "text": "..."}, ...],
             "truncated": false}
        """
        target = _safe_project_path(directory) if directory else _PROJECT_ROOT
        if not target.exists():
            return {"matches": [], "truncated": False}
        if not target.is_dir():
            raise ValueError(f"Path is not a directory: {directory!r}")

        try:
            compiled = re.compile(pattern)
        except re.error as e:
            raise ValueError(f"Invalid regex pattern: {e}")

        resolved_root = _PROJECT_ROOT.resolve()
        matches: list[dict[str, str | int]] = []

        for filepath in sorted(target.resolve().rglob("*")):
            if not filepath.is_file():
                continue
            rel = filepath.relative_to(resolved_root)
            if _is_excluded_dir(rel) or _is_binary(filepath):
                continue

            try:
                text = filepath.read_text(encoding="utf-8", errors="replace")
            except (OSError, PermissionError) as e:
                logger.debug("Skipping unreadable file %s: %s", filepath, e)
                continue

            for line_num, line in enumerate(text.splitlines(), start=1):
                if compiled.search(line):
                    matches.append({
                        "file": str(rel),
                        "line": line_num,
                        "text": line.rstrip(),
                    })
                    if len(matches) >= max_results:
                        break
            if len(matches) >= max_results:
                break

        truncated = len(matches) >= max_results
        logger.info(
            "search_project_files: pattern=%r dir=%s → %d matches%s",
            pattern,
            directory or "/",
            len(matches),
            " (truncated)" if truncated else "",
        )
        return {"matches": matches, "truncated": truncated}
