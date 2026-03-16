"""Project directory tools — read + optional write access to mounted project(s).

All paths are resolved relative to _PROJECT_ROOT. Traversal attempts
(../) are rejected before resolution so that the container volume mount
is complemented by explicit code-level defense in depth.

Supports both single-repo roots (PROJECT_ROOT is a git repo) and
multi-repo roots (PROJECT_ROOT contains multiple git repos as children).

File listing uses `git ls-files` so only tracked files appear — .gitignore
is the single source of truth for what's visible. Binary files are excluded
from listing and rejected from reading.

Write tools (write_project_file, edit_project_file) are gated by
PROJECT_READ_ONLY (default true). Toggle via project_access in mcp.yaml.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, cast

from mcp_events import record

from .file_editor import perform_edit

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(os.environ.get("PROJECT_ROOT", "/data/project"))
_PROJECT_READ_ONLY = os.environ.get("PROJECT_READ_ONLY", "true").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}

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

_WRITABLE_SUFFIXES = {
    ".py",
    ".md",
    ".txt",
    ".yaml",
    ".yml",
    ".json",
    ".toml",
    ".html",
    ".css",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".sh",
    ".cfg",
    ".ini",
    ".env",
    ".mdc",
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


def _read_only_error() -> dict[str, str]:
    return {
        "error": (
            "project is read-only (PROJECT_READ_ONLY=true); "
            "set project_access: rw in ~/.gateway/mcp.yaml and rebuild MCP"
        )
    }


def _discover_repos() -> list[Path]:
    """Find git repos within the project root (depth 0 or 1)."""
    root = _PROJECT_ROOT.resolve()
    if (root / ".git").exists():
        return [root]
    repos = []
    try:
        children = sorted(root.iterdir())
    except OSError:
        return []
    for child in children:
        if child.is_dir() and (child / ".git").exists():
            repos.append(child)
    return repos


def _git_ls_files_in_repo(
    repo: Path,
    sub_dir: str = "",
) -> list[str]:
    """Run git ls-files in a single repo, return paths relative to repo root."""
    cmd = ["git", "-C", str(repo), "ls-files"]
    if sub_dir:
        cmd.extend(["--", sub_dir])
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
            "git ls-files failed in %s (rc=%d): %s",
            repo,
            result.returncode,
            result.stderr.strip(),
        )
        return []
    return [f for f in result.stdout.splitlines() if f]


def _git_tracked_files(directory: str = "") -> list[str]:
    """Return git-tracked file paths relative to PROJECT_ROOT.

    Handles both single-repo and multi-repo project roots. For multi-repo,
    discovers child repos and aggregates results with repo-relative prefixes.
    """
    resolved_root = _PROJECT_ROOT.resolve()
    repos = _discover_repos()
    if not repos:
        return []

    all_files: list[str] = []
    for repo in repos:
        repo_rel = str(repo.relative_to(resolved_root))
        is_root_repo = repo_rel == "."

        if directory:
            if is_root_repo:
                sub_dir = directory
            elif directory == repo_rel or directory.startswith(f"{repo_rel}/"):
                sub_dir = directory[len(repo_rel) :].lstrip("/")
            else:
                continue
        else:
            sub_dir = ""

        files = _git_ls_files_in_repo(repo, sub_dir)
        prefix = "" if is_root_repo else f"{repo_rel}/"
        all_files.extend(f"{prefix}{f}" for f in files)

    return all_files


def register_project_tools(mcp: FastMCP) -> None:
    """Register project directory tools on *mcp*."""

    @mcp.tool()
    def read_project_file(path: str) -> dict[str, str]:
        """Read a file from the project directory.

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

        Supports multi-repo project roots: if the project root contains
        multiple git repos, files are listed across all of them with
        repo-relative prefixes (e.g. "agent-bus/src/main.py").

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

    @mcp.tool()
    def write_project_file(path: str, content: str) -> dict[str, str]:
        """Write or create a file in the project directory.

        Requires project_access: rw in ~/.gateway/mcp.yaml (rebuild MCP after change).
        Creates parent directories as needed. Only text file types are writable.

        Args:
            path: Relative file path, e.g. "agent-bus/src/new_module.py".
            content: Full file content to write.

        Returns:
            {"status": "written", "path": "<relative path>"}
        """
        if _PROJECT_READ_ONLY:
            return _read_only_error()

        target = _safe_project_path(path)
        suffix = target.suffix.lower()
        if suffix and suffix not in _WRITABLE_SUFFIXES:
            return {"error": f"File type {suffix!r} is not writable"}
        if _is_binary(target):
            return {"error": f"Binary file type {suffix!r} cannot be written"}

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        rel = str(target.relative_to(_PROJECT_ROOT.resolve()))
        logger.info("write_project_file: %s (%d chars)", rel, len(content))
        record("mcp.project.file.written", path=rel, size=len(content))
        return {"status": "written", "path": rel}

    @mcp.tool()
    def edit_project_file(
        path: str,
        operation: str,
        content: str,
        target_str: str = "",
        line: int = 0,
        all_occurrences: bool = False,
    ) -> dict[str, str | int]:
        """Edit an existing file in the project directory.

        Requires project_access: rw in ~/.gateway/mcp.yaml (rebuild MCP after change).

        Operations:
          - "prepend": insert content at the beginning of the file
          - "append": insert content at the end of the file
          - "insert_at_line": insert content at a specific line number (1-indexed)
          - "replace": find target_str and replace with content

        Args:
            path: Relative file path to edit.
            operation: One of "prepend", "append", "insert_at_line", "replace".
            content: Text to insert or replacement text.
            target_str: String to find (required for "replace" operation).
            line: Line number for "insert_at_line" (1-indexed, required for that op).
            all_occurrences: For "replace": replace all matches vs first only.

        Returns:
            {"status": "edited: <operation>", "path": "<relative path>"}
            For replace: includes "replacements_made".
        """
        if _PROJECT_READ_ONLY:
            return cast(dict[str, str | int], _read_only_error())

        target = _safe_project_path(path)
        if not target.exists():
            return cast(dict[str, str | int], {"error": f"File not found: {path!r}"})
        if _is_binary(target):
            return cast(
                dict[str, str | int],
                {"error": f"Binary file type {target.suffix!r} cannot be edited"},
            )

        result = perform_edit(
            target,
            operation,
            content,
            line=line if line else None,
            target_str=target_str if target_str else None,
            all_occurrences=all_occurrences,
        )
        rel = str(target.relative_to(_PROJECT_ROOT.resolve()))
        logger.info("edit_project_file: %s op=%s", rel, operation)
        record("mcp.project.file.edited", path=rel, operation=operation)
        return result
