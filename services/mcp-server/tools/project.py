"""Project directory tools — read-only access to the mounted project.

All paths are resolved relative to _PROJECT_ROOT. Traversal attempts
(../) are rejected. The volume mount is :ro but code-level enforcement
provides defense in depth — no write functions exist.

File listing uses `git ls-files` so only tracked files appear — .gitignore
is the single source of truth for what's visible. Binary files are excluded
from listing and rejected from reading.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(os.environ.get("PROJECT_ROOT", "/data/project"))

_BINARY_SUFFIXES = {
    ".pyc",
    ".pyo",
    ".so",
    ".dll",
    ".dylib",
    ".o",
    ".a",
    ".whl",
    ".egg",
    ".gz",
    ".tar",
    ".zip",
    ".bz2",
    ".xz",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".bmp",
    ".ico",
    ".webp",
    ".svg",
    ".mp3",
    ".mp4",
    ".wav",
    ".avi",
    ".mov",
    ".bin",
    ".dat",
    ".db",
    ".sqlite",
    ".sqlite3",
    ".pkl",
    ".pickle",
    ".npy",
    ".npz",
    ".gguf",
    ".ggml",
    ".safetensors",
    ".ttf",
    ".otf",
    ".woff",
    ".woff2",
    ".pdf",
}


def _safe_project_path(relative: str) -> Path:
    """Resolve *relative* inside the project root, rejecting traversal."""
    clean = relative.lstrip("/")
    resolved_root = _PROJECT_ROOT.resolve()
    candidate = (resolved_root / clean).resolve()
    try:
        candidate.relative_to(resolved_root)
    except ValueError:
        raise ValueError(
            f"Path {relative!r} resolves outside project root; traversal rejected"
        )
    return candidate


def _is_binary(path: Path) -> bool:
    """Return True if the file's suffix indicates binary content."""
    return path.suffix.lower() in _BINARY_SUFFIXES


def _git_tracked_files(directory: str = "") -> list[str]:
    """Return git-tracked file paths relative to PROJECT_ROOT.

    Uses ``git -C <root> ls-files`` so the .git directory must be
    accessible inside the container mount.  GIT_OPTIONAL_LOCKS=0
    prevents lock-file creation on the read-only mount.
    Falls back to an empty list on failure.
    """
    resolved = str(_PROJECT_ROOT.resolve())
    cmd = ["git", "-C", resolved, "ls-files"]
    if directory:
        cmd.extend(["--", directory])

    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
        )
    except FileNotFoundError:
        logger.warning("git command not found or inaccessible.")
        return []
    except subprocess.TimeoutExpired as e:
        logger.warning("git ls-files timed out: %s", e)
        return []

    if result.returncode != 0:
        logger.warning(
            "git ls-files failed (rc=%d): %s",
            result.returncode,
            result.stderr.strip(),
        )
        return []

    return [f for f in result.stdout.splitlines() if f]


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
        """List git-tracked files in the project directory.

        Only files tracked by git are shown — .gitignore is respected.
        Binary files are excluded. Use max_depth to limit how deep the
        listing goes (default 3).

        Args:
            directory: Relative directory path. Empty string lists the project root.
            max_depth: Maximum directory depth to recurse (1 = immediate children only).

        Returns:
            {"files": ["<relative paths>", ...], "truncated": false}
        """
        if directory:
            target = _safe_project_path(directory)
            if not target.is_dir():
                raise ValueError(f"Path is not a directory: {directory!r}")

        tracked = _git_tracked_files(directory)
        base_parts = len(Path(directory).parts) if directory else 0
        cap = 2000

        files: list[str] = []
        for f in tracked:
            depth = len(Path(f).parts) - base_parts
            if depth > max_depth:
                continue
            if _is_binary(Path(f)):
                continue
            files.append(f)
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
        """Search for an exact regex pattern across git-tracked project files.

        This is literal/regex text search — use rag_search(scope="project")
        for semantic search when you need meaning-based retrieval.

        Only files tracked by git are searched — .gitignore is respected.
        Binary files are skipped.

        Args:
            pattern: Regex pattern to search for (case-sensitive).
            directory: Relative directory to search within. Empty = project root.
            max_results: Maximum number of matching lines to return (default 50).

        Returns:
            {"matches": [{"file": "...", "line": N, "text": "..."}, ...],
             "truncated": false}
        """
        if directory:
            target = _safe_project_path(directory)
            if not target.is_dir():
                raise ValueError(f"Path is not a directory: {directory!r}")

        try:
            compiled = re.compile(pattern)
        except re.error as e:
            raise ValueError(f"Invalid regex pattern: {e}")

        resolved_root = _PROJECT_ROOT.resolve()
        tracked = _git_tracked_files(directory)
        matches: list[dict[str, str | int]] = []

        for rel_path in tracked:
            if _is_binary(Path(rel_path)):
                continue
            abs_path = resolved_root / rel_path
            try:
                text = abs_path.read_text(encoding="utf-8", errors="replace")
            except (OSError, PermissionError) as e:
                logger.warning("Failed to read file %s for search: %s", abs_path, e)
                continue

            for line_num, line in enumerate(text.splitlines(), start=1):
                if compiled.search(line):
                    matches.append(
                        {
                            "file": rel_path,
                            "line": line_num,
                            "text": line.rstrip(),
                        }
                    )
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
