"""Project directory tools — read + optional write access to mounted project(s).

All paths are resolved relative to _PROJECT_ROOT. Traversal attempts
(../) are rejected before resolution so that the container volume mount
is complemented by explicit code-level defense in depth.

Supports both single-repo roots (PROJECT_ROOT is a git repo) and
multi-repo roots (PROJECT_ROOT contains multiple git repos as children).

File listing walks the real filesystem by default so all files are visible
(including gitignored directories like tmp/). Pass include_untracked=False
to restrict to git-tracked files. Binary assets are excluded from default
listing, but text-oriented document formats handled by the shared file reader
(`.pdf`, `.docx`, `.odt`, `.eml`, `.html`) can be read in text mode.

Write tools (write_project_file, edit_project_file) are gated by
PROJECT_READ_ONLY (default true). Toggle via project_access in mcp.yaml.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from mcp_events import record

from ._file_helpers import read_file_result
from .file_editor import perform_edit
from .filesystem._paths import _SANDBOX_ROOT, _trash_destination

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

_LIST_CAP = 2000


def _safe_project_path(relative: str) -> Path:
    """Resolve *relative* inside the project root, rejecting traversal."""
    clean = relative.lstrip("/")
    resolved_root = _PROJECT_ROOT  # Assuming _PROJECT_ROOT is already resolved
    candidate = resolved_root / clean
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


def _read_only_error() -> dict[str, str | int]:
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
    repos = [
        child
        for child in sorted(root.iterdir())
        if child.is_dir() and (child / ".git").exists()
    ]
    return repos
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


def _filesystem_listing(
    directory: str = "",
    max_depth: int | None = None,
    *,
    skip_binary: bool = True,
) -> tuple[list[str], list[str], bool]:
    """Return files and directories via filesystem walk.

    Unlike ``_git_tracked_files``, this enumerates the actual filesystem so
    untracked and empty directories remain visible. Common build/cache
    directories are always skipped. Binary files are skipped by default but
    can be included for listing operations where only file paths are returned.
    """
    resolved_root = _PROJECT_ROOT.resolve()
    base = _safe_project_path(directory) if directory else resolved_root

    if not base.is_dir():
        return [], [], False

    skip_dirs = {
        ".git",
        "__pycache__",
        ".venv",
        "venv",
        "node_modules",
        ".ruff_cache",
        ".mypy_cache",
        ".pytest_cache",
    }
    base_depth = len(base.parts)
    files: list[str] = []
    directories: list[str] = []
    total_entries = 0

    for dirpath_str, dirnames, filenames in os.walk(base):
        dirpath = Path(dirpath_str)
        dirnames[:] = [d for d in sorted(dirnames) if d not in skip_dirs]

        dir_depth = len(dirpath.parts) - base_depth
        if dir_depth > 0:
            directories.append(str(dirpath.relative_to(resolved_root)))
            total_entries += 1
            if total_entries >= _LIST_CAP:
                return files, directories, True
        if max_depth is not None and dir_depth >= max_depth:
            dirnames.clear()

        for fname in sorted(filenames):
            fpath = dirpath / fname
            file_depth = len(fpath.parts) - base_depth
            if max_depth is not None and file_depth > max_depth:
                continue
            if skip_binary and _is_binary(fpath):
                continue
            rel = str(fpath.relative_to(resolved_root))
            files.append(rel)
            total_entries += 1
            if total_entries >= _LIST_CAP:
                return files, directories, True

    return files, directories, False


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


def _tracked_parent_directories(
    directory: str,
    files: list[str],
    *,
    max_depth: int,
) -> list[str]:
    """Derive directory prefixes for tracked files relative to PROJECT_ROOT."""
    base_prefix = Path(directory) if directory else Path()
    directories: set[str] = set()

    for file_path in files:
        try:
            relative_to_base = (
                Path(file_path).relative_to(base_prefix)
                if directory
                else Path(file_path)
            )
        except ValueError:
            continue
        current = Path()
        for depth, part in enumerate(relative_to_base.parts[:-1], start=1):
            if depth > max_depth:
                break
            current /= part
            project_relative = base_prefix / current if directory else current
            directories.add(str(project_relative))

    return sorted(directories)


def register_project_tools(mcp: FastMCP) -> None:
    """Register project directory tools on *mcp*."""

    @mcp.tool(title="Read Project File")
    def read_project_file(path: str, binary: bool = False) -> dict[str, Any]:
        """Read a file from the project directory.

        Use the default text mode for source files, Markdown, and supported
        document formats. Project reads share the same extraction helpers as the
        cortex sandbox, so PDFs, DOCX, ODT, EML, and HTML can be read as text.
        Image, audio, video, and archive files auto-route to base64 even without
        ``binary=True`` — reading a ``.png``, ``.jpg``, or archive returns
        ``content_base64`` with ``auto_binary: true`` rather than corrupted text.
        Pass ``binary=True`` explicitly to force base64 for any file type.

        Args:
            path: Relative file path within the project, e.g. "services/mcp-server/server.py".
            binary: If True, return base64 bytes instead of decoded text.
                Image, audio, video, and archive files auto-route to binary even when False.

        Returns:
            Text mode: {"content": "<file contents>", "path": "<relative path>"}
            Binary mode: {"content_base64", "mime_type", "encoding", "bytes", "path",
                "auto_binary": true (when auto-routed)}
        """
        src = _safe_project_path(path)
        if not src.exists():
            raise FileNotFoundError(f"File not found: {path!r}")
        if not src.is_file():
            raise ValueError(f"Path is not a file: {path!r}")

        result = read_file_result(path, root=_PROJECT_ROOT, binary=binary)
        result["path"] = path
        if binary:
            logger.info(
                "read_project_file: %s (%d bytes, binary)", path, result["bytes"]
            )
        else:
            logger.info(
                "read_project_file: %s (%d chars)", path, len(result["content"])
            )
        return result

    @mcp.tool(title="Move Project File")
    def move_project_file(path: str, target: str) -> dict[str, str]:
        """Move or rename a file in the project directory.

        Requires project_access: rw in ~/.gateway/mcp.yaml (rebuild MCP after change).
        """
        if _PROJECT_READ_ONLY:
            return cast("dict[str, str]", _read_only_error())

        src = _safe_project_path(path)
        dst = _safe_project_path(target)
        if not src.exists():
            return {"error": f"File not found: {path!r}"}
        if not src.is_file():
            return {"error": f"Path is not a file: {path!r}"}

        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        rel_src = str(src.relative_to(_PROJECT_ROOT.resolve()))
        rel_dst = str(dst.relative_to(_PROJECT_ROOT.resolve()))
        logger.info("move_project_file: %s -> %s", rel_src, rel_dst)
        record("mcp.project.file.moved", source=rel_src, destination=rel_dst)
        return {"status": "moved", "from": rel_src, "to": rel_dst}

    @mcp.tool(title="Copy Project File")
    def copy_project_file(path: str, target: str) -> dict[str, str]:
        """Copy a file in the project directory.

        Requires project_access: rw in ~/.gateway/mcp.yaml (rebuild MCP after change).
        Creates intermediate directories at the destination automatically.
        Overwrites the destination if it already exists.

        Args:
            path: Relative source path, e.g. "universal-llm-gateway/docs/foo.md".
            target: Relative destination path.

        Returns:
            {"status": "copied", "from": "<source path>", "to": "<dest path>"}
        """
        if _PROJECT_READ_ONLY:
            return cast("dict[str, str]", _read_only_error())

        src = _safe_project_path(path)
        dst = _safe_project_path(target)
        if not src.exists():
            return {"error": f"File not found: {path!r}"}
        if not src.is_file():
            return {"error": f"Path is not a file: {path!r}"}

        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(src), str(dst))
        rel_src = str(src.relative_to(_PROJECT_ROOT.resolve()))
        rel_dst = str(dst.relative_to(_PROJECT_ROOT.resolve()))
        logger.info("copy_project_file: %s -> %s", rel_src, rel_dst)
        record("mcp.project.file.copied", source=rel_src, destination=rel_dst)
        return {"status": "copied", "from": rel_src, "to": rel_dst}

    @mcp.tool(title="Delete Project File")
    def delete_project_file(path: str) -> dict[str, str]:
        """Soft-delete a project file by moving it to the cortex trash/ directory.

        Uses the same trash location and collision-resolution logic as cortex
        sandbox deletes — /data/files/trash/<path>. Restore via:
          fs(sandbox='cortex', op='move', path='trash/<path>', target='<path>')

        Requires project_access: rw in ~/.gateway/mcp.yaml (rebuild MCP after change).

        Args:
            path: Relative path including repo prefix,
                e.g. "universal-llm-gateway/tmp/old-plan.md".

        Returns:
            {"status": "trashed", "path": "<source>", "trash_path": "trash/<path>"}
        """
        if _PROJECT_READ_ONLY:
            return cast("dict[str, str]", _read_only_error())

        src = _safe_project_path(path)
        if not src.exists():
            return {"error": f"File not found: {path!r}"}
        if not src.is_file():
            return {
                "error": f"Path is not a file (directories not supported): {path!r}"
            }

        dest = _trash_destination(path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest))
        trash_rel = str(dest.relative_to(_SANDBOX_ROOT))
        logger.info("delete_project_file: trashed %s → %s", src, dest)
        record(
            "mcp.tool.file.trashed",
            sandbox="workspaces",
            path=path,
            trash_path=trash_rel,
        )
        return {"status": "trashed", "path": path, "trash_path": trash_rel}

    @mcp.tool(title="List Project Files")
    def list_project_files(
        directory: str = "",
        max_depth: int = 3,
        include_untracked: bool = True,
    ) -> dict[str, list[str] | bool]:
        """List files in the project directory.

        By default lists ALL files and directories on disk (including
        gitignored and untracked paths). Set include_untracked=False to
        restrict file discovery to git-tracked files while still surfacing
        parent directories needed for navigation.

        All file types are listed (including PDFs and other binary files).
        Text-mode reads support source files plus extracted document formats
        like PDF, DOCX, ODT, EML, and HTML. Use ``binary=True`` for raw bytes.
        Use max_depth to limit recursion depth (default 3, where 1 = immediate
        children only).

        Supports multi-repo project roots: if the project root contains
        multiple git repos, files are listed across all of them with
        repo-relative prefixes (e.g. "agent-bus/src/main.py").

        Args:
            directory: Relative directory path. Empty string lists the project root.
            max_depth: Maximum directory depth to recurse (1 = immediate children only).
            include_untracked: If True, list all files (not just git-tracked).

        Returns:
            {
              "files": ["<relative file paths>", ...],
              "directories": ["<relative directory paths>", ...],
              "truncated": false,
            }
        """
        if directory:
            target = _safe_project_path(directory)
            if not target.is_dir():
                raise ValueError(f"Path is not a directory: {directory!r}")

        if include_untracked:
            files, directories, truncated = _filesystem_listing(
                directory,
                max_depth=max_depth,
                skip_binary=False,
            )
        else:
            tracked = _git_tracked_files(directory)
            base_path_obj = (
                _safe_project_path(directory) if directory else _PROJECT_ROOT.resolve()
            )
            files = []
            for f in tracked:
                # Ensure path is relative to the directory being listed, not just PROJECT_ROOT
                full_path = _PROJECT_ROOT.resolve() / f
                try:
                    relative_to_base = full_path.relative_to(base_path_obj)
                    depth = len(relative_to_base.parts)
                except ValueError:
                    # File is not within the specified directory, skip
                    continue

                if depth > max_depth:
                    continue
                files.append(f)
                if len(files) >= _LIST_CAP:
                    break

            directories = _tracked_parent_directories(
                directory,
                tracked,
                max_depth=max_depth,
            )
            truncated = len(files) >= _LIST_CAP
        logger.info(
            "list_project_files: %s depth=%d → %d files, %d dirs%s",
            directory or "/",
            max_depth,
            len(files),
            len(directories),
            " (truncated)" if truncated else "",
        )
        return {"files": files, "directories": directories, "truncated": truncated}

    @mcp.tool(title="Search Project Files")
    def search_project_files(
        pattern: str,
        directory: str = "",
        max_results: int = 50,
        include_untracked: bool = True,
    ) -> dict[str, list[dict[str, str | int]] | bool]:
        """Search for an exact regex pattern across project files.

        This is literal/regex text search — use rag(op="search", arguments={...})
        with scope="project" when you need meaning-based retrieval.

        By default searches ALL files on disk (including gitignored directories
        like tmp/, prompts/, build artifacts). Set include_untracked=False
        to restrict to git-tracked files only.

        Binary files are always skipped.

        Args:
            pattern: Regex pattern to search for (case-sensitive).
            directory: Relative directory to search within. Empty = project root.
            max_results: Maximum number of matching lines to return (default 50).
            include_untracked: If True, search all files (not just git-tracked).

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
        if include_untracked:
            tracked, _, _ = _filesystem_listing(directory)
        else:
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

    @mcp.tool(title="Write Project File")
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

    @mcp.tool(title="Edit Project File")
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
            return cast("dict[str, str | int]", _read_only_error())

        target = _safe_project_path(path)
        if not target.exists():
            return cast("dict[str, str | int]", {"error": f"File not found: {path!r}"})
        if _is_binary(target):
            return cast(
                "dict[str, str | int]",
                {"error": f"Binary file type {target.suffix!r} cannot be edited"},
            )

        if operation == "insert_at_line" and line <= 0:
            return cast(
                "dict[str, str | int]",
                {"error": "Line number must be 1 or greater for 'insert_at_line'"},
            )
        if operation == "replace" and not target_str:
            return cast(
                "dict[str, str | int]",
                {"error": "'target_str' cannot be empty for 'replace' operation"},
            )

        result = perform_edit(
            target,
            operation,
            content,
            line=line if line > 0 else None,
            target_str=target_str if target_str else None,
            all_occurrences=all_occurrences,
        )
        rel = str(target.relative_to(_PROJECT_ROOT.resolve()))
        logger.info("edit_project_file: %s op=%s", rel, operation)
        record("mcp.project.file.edited", path=rel, operation=operation)
        return result
