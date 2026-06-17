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

Write tools (write_project_file, edit_project_file) operate unconditionally;
the workspaces sandbox is mounted read/write.
"""

from __future__ import annotations

import fnmatch
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from mcp_events import record
from universal_logging import get_logger

from ._durable_write import (
    WriteVerifyError,
    durable_write_text,
    verify_persisted,
    write_verify_error_dict,
)
from ._file_helpers import load_searchable_text, read_file_result
from ._project_paths import (
    multi_repo_root_unscoped,
    normalize_directory_arg,
    resolve_existing_file,
    workspaces_relative,
)
from ._search_helpers import (
    SEARCH_BINARY_SUFFIXES,
    SearchBudgetState,
    load_text_for_search_file,
)
from .file_editor import perform_edit
from .filesystem._ops_search import (
    DEFAULT_MAX_RESULTS,
    SEARCH_CONVERTED_BUDGET_S,
    SEARCH_CONVERTED_FILE_CAP,
    compile_pattern,
    search_in_text,
)
from .filesystem._paths import SANDBOX_ROOT, trash_destination

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = get_logger(__name__)

_PROJECT_ROOT = Path(os.environ.get("PROJECT_ROOT", "/data/project"))

_BINARY_SUFFIXES = (
    SEARCH_BINARY_SUFFIXES  # listing/write gate; search uses _search_helpers
)

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
    resolved_root = _PROJECT_ROOT.resolve()
    candidate = (resolved_root / clean).resolve()
    try:
        candidate.relative_to(resolved_root)
    except ValueError:
        raise ValueError(
            f"Path {relative!r} resolves outside project root; traversal rejected"
        )
    return candidate


def _resolve_project_file_path(relative: str) -> tuple[Path, str]:
    """Resolve a readable file, including repo-relative refs without repo prefix."""
    resolved = resolve_existing_file(relative, root=_PROJECT_ROOT.resolve())
    if resolved is not None:
        rel = workspaces_relative(resolved, _PROJECT_ROOT.resolve())
        return resolved, rel
    return _safe_project_path(relative), relative.lstrip("/")


_LITERAL_FILENAME_PATTERN = re.compile(r"^[\w./-]+$")
_REGEX_METACHAR_PATTERN = re.compile(r"[\\^$|+()\[\]{}]")
# A literal-looking token is only treated as a filename when it carries a
# filename signal: a path separator (`/`) or an extension-like trailing dot
# (`.py`, `.md`). A bare identifier word ("provenance", "handoff_provenance")
# is a legitimate content-search term, NOT a filename — routing it to filename
# `find` silently returns matches:[] for a string that is actually present
# (agent-bus:1193 hazard 1). When filename lookup is intended, op=find exists.
_FILENAME_SIGNAL_PATTERN = re.compile(r"/|\.\w+$")


def _looks_like_literal_filename(pattern: str) -> bool:
    return (
        bool(_LITERAL_FILENAME_PATTERN.match(pattern))
        and not _REGEX_METACHAR_PATTERN.search(pattern)
        and bool(_FILENAME_SIGNAL_PATTERN.search(pattern))
    )


def _is_binary(path: Path) -> bool:
    """Return True if the file's suffix indicates binary content."""
    return path.suffix.lower() in _BINARY_SUFFIXES


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
    cap: int | None = _LIST_CAP,
) -> tuple[list[str], list[str], bool]:
    """Return files and directories via filesystem walk.

    Unlike ``_git_tracked_files``, this enumerates the actual filesystem so
    untracked and empty directories remain visible. Common build/cache
    directories are always skipped. Binary files are skipped by default but
    can be included for listing operations where only file paths are returned.

    ``cap`` bounds the number of enumerated entries. The ``list`` op keeps the
    display cap (``_LIST_CAP``); ``find``/``search`` pass ``cap=None`` so the
    walk is exhaustive. A fixed cap silently truncated the alphabetical walk
    mid-tree, dropping later directories (e.g. ``scripts/``, which sorts after
    ``.runtime/`` and ``libs/``) from the candidate set and producing
    false-negative ``find``/``search`` results (friction 13196).
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
            if cap is not None and total_entries >= cap:
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
            if cap is not None and total_entries >= cap:
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
    def read_project_file(
        path: str, binary: bool = False, offset: int = 0, limit: int = 0
    ) -> dict[str, Any]:
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
        src, rel_path = _resolve_project_file_path(path)
        if not src.exists():
            raise FileNotFoundError(f"File not found: {path!r}")
        if not src.is_file():
            raise ValueError(f"Path is not a file: {path!r}")

        result = read_file_result(
            rel_path, root=_PROJECT_ROOT, binary=binary, offset=offset, limit=limit
        )
        result["path"] = rel_path
        if rel_path != path.lstrip("/"):
            result["resolved_from"] = path
        auto_binary = bool(result.get("auto_binary"))
        if binary or auto_binary:
            logger.info(
                "read_project_file: %s (%d bytes, binary)", path, result.get("bytes", 0)
            )
        else:
            logger.info(
                "read_project_file: %s (%d chars)", path, len(result.get("content", ""))
            )
        return result

    @mcp.tool(title="Move Project File")
    def move_project_file(path: str, target: str) -> dict[str, str]:
        """Move or rename a file in the project directory."""
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

        Creates intermediate directories at the destination automatically.
        Overwrites the destination if it already exists.

        Args:
            path: Relative source path, e.g. "universal-llm-gateway/docs/foo.md".
            target: Relative destination path.

        Returns:
            {"status": "copied", "from": "<source path>", "to": "<dest path>"}
        """
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

        Args:
            path: Relative path including repo prefix,
                e.g. "universal-llm-gateway/tmp/old-plan.md".

        Returns:
            {"status": "trashed", "path": "<source>", "trash_path": "trash/<path>"}
        """
        src = _safe_project_path(path)
        if not src.exists():
            return {"error": f"File not found: {path!r}"}
        if not src.is_file():
            return {
                "error": f"Path is not a file (directories not supported): {path!r}"
            }

        dest = trash_destination(path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest))
        trash_rel = str(dest.relative_to(SANDBOX_ROOT))
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
        directory = normalize_directory_arg(directory)
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

    @mcp.tool(title="Find Project Files")
    def find_project_files(
        pattern: str,
        directory: str = "",
        max_depth: int | None = None,
        max_results: int = DEFAULT_MAX_RESULTS,
    ) -> dict[str, Any]:
        """Find files by glob-style name under a scoped directory.

        *pattern* is matched against each file's basename and full repo-relative
        path. If it contains no ``*`` or ``?``, ``*{pattern}*`` is used.

        Depth is unbounded by default (``max_depth=None``): a filename find must
        reach matches at any depth, so it runs full-depth and is not constrained
        by the list-oriented ``max_depth`` default that the fs tool applies to
        ``list``. ``search`` is likewise unbounded; both rely on ``max_results``
        to bound the result set. Deep matches under e.g.
        scripts/model_manager/ui/ were previously dropped because the list
        default (depth 3) was propagated to find (friction 13196).

        Prefer this over ``search`` when locating a file by name; ``search`` scans
        file *contents* with a regex and is expensive at the workspaces root.
        """
        directory = normalize_directory_arg(directory)
        if multi_repo_root_unscoped(_PROJECT_ROOT.resolve()) and not directory:
            return {
                "error": (
                    "find at workspaces root without a repo prefix is too broad. "
                    "Scope path to a repo, e.g. path='universal-llm-gateway'."
                ),
                "hint": (
                    "fs(op='find', sandbox='workspaces', "
                    "path='universal-llm-gateway', content='session_handoff.py')"
                ),
            }
        glob_pat = pattern if any(ch in pattern for ch in "*?[]") else f"*{pattern}*"
        if directory:
            target = _safe_project_path(directory)
            if not target.is_dir():
                if target.is_file():
                    rel = workspaces_relative(target, _PROJECT_ROOT.resolve())
                    matched = fnmatch.fnmatch(target.name, glob_pat) or fnmatch.fnmatch(
                        rel, glob_pat
                    )
                    paths = [rel] if matched else []
                    return {
                        "path": directory,
                        "mode": "file",
                        "matches": paths,
                        "truncated": False,
                        "pattern": glob_pat,
                    }
                raise ValueError(f"Path is not a directory: {directory!r}")

        files, _, _ = _filesystem_listing(
            directory,
            max_depth=max_depth,
            skip_binary=False,
            cap=None,
        )
        matches: list[str] = []
        truncated = False
        for rel_path in files:
            base = Path(rel_path).name
            if fnmatch.fnmatch(base, glob_pat) or fnmatch.fnmatch(rel_path, glob_pat):
                matches.append(rel_path)
                if len(matches) >= max_results:
                    truncated = True
                    break
        logger.info(
            "find_project_files: pattern=%r dir=%s → %d paths%s",
            glob_pat,
            directory or "/",
            len(matches),
            " (truncated)" if truncated else "",
        )
        return {
            "path": directory,
            "mode": "directory",
            "matches": matches,
            "match_count": len(matches),
            "truncated": truncated,
            "pattern": glob_pat,
        }

    @mcp.tool(title="Search Project Files")
    def search_project_files(
        pattern: str,
        directory: str = "",
        max_results: int = DEFAULT_MAX_RESULTS,
        include_untracked: bool = True,
    ) -> dict[str, Any]:
        """Search for an exact regex pattern across project files.

        This is literal/regex text search — use rag(op="search", arguments={...})
        with scope="project" when you need meaning-based retrieval.

        By default searches ALL files on disk (including gitignored directories
        like tmp/, prompts/, build artifacts). Set include_untracked=False
        to restrict to git-tracked files only.

        Converted documents (PDF/DOCX/ODT/EML/HTML) are searched via the
        shared sidecar-first text loader — converted ≠ truly-binary
        (narrows decision:mcp-list-include-binary-paths / agent-bus:188).
        Converted-file extraction is bounded by an aggregate wall-clock budget
        and a per-call converted-file cap; files beyond either bound are
        reported in ``skipped_converted``. Truly-binary files (images,
        archives, compiled artifacts) are skipped.

        Args:
            pattern: Regex pattern to search for (case-sensitive).
            directory: Relative directory to search within. Empty = project root.
            max_results: Maximum number of matching lines to return (default 50).
            include_untracked: If True, search all files (not just git-tracked).

        Returns:
            Unified search envelope with ``mode`` ``file`` or ``directory``.
        """
        directory = normalize_directory_arg(directory)
        if _looks_like_literal_filename(pattern):
            return find_project_files(
                pattern,
                directory,
                max_results=max_results,
            )
        if (
            multi_repo_root_unscoped(resolved_root := _PROJECT_ROOT.resolve())
            and not directory
        ):
            return {
                "error": (
                    "search at workspaces root without a repo prefix scans every "
                    "mounted repo and may time out."
                ),
                "hint": (
                    "Scope path to a repo (path='universal-llm-gateway') or use "
                    "fs(op='find', content='filename.py') for name lookup."
                ),
            }
        compiled = compile_pattern(pattern)
        resolved_root = _PROJECT_ROOT.resolve()

        if directory:
            target = _safe_project_path(directory)
            if not target.exists():
                raise FileNotFoundError(f"Path not found: {directory!r}")
            if target.is_file():
                text, method = load_searchable_text(target)
                matches: list[dict[str, str | int]] = []
                truncated = search_in_text(
                    text,
                    compiled,
                    matches,
                    rel_path=None,
                    max_results=max_results,
                )
                extraction_method = method or "native_text"
                logger.info(
                    "search_project_files: pattern=%r file=%s → %d matches%s",
                    pattern,
                    directory,
                    len(matches),
                    " (truncated)" if truncated else "",
                )
                return {
                    "path": directory,
                    "mode": "file",
                    "matches": matches,
                    "truncated": truncated,
                    "skipped_converted": 0,
                    "extraction_method": extraction_method,
                }
            if not target.is_dir():
                raise ValueError(f"Path is not a file or directory: {directory!r}")

        # Enumeration layer (F2/188): skip_binary=False so converted formats
        # enter the candidate list; load_text_for_search_file filters per file.
        if include_untracked:
            candidates, _, _ = _filesystem_listing(
                directory, skip_binary=False, cap=None
            )
        else:
            candidates = _git_tracked_files(directory)

        matches = []
        state = SearchBudgetState()
        truncated = False

        for rel_path in candidates:
            abs_path = resolved_root / rel_path
            text, _method = load_text_for_search_file(
                abs_path,
                state,
                budget_s=SEARCH_CONVERTED_BUDGET_S,
                file_cap=SEARCH_CONVERTED_FILE_CAP,
            )
            if text is None:
                continue

            if search_in_text(
                text, compiled, matches, rel_path=rel_path, max_results=max_results
            ):
                truncated = True
                break

        logger.info(
            "search_project_files: pattern=%r dir=%s → %d matches "
            "(converted=%d, skipped_converted=%d)%s",
            pattern,
            directory or "/",
            len(matches),
            state.converted_extracted,
            state.skipped_converted,
            " (truncated)" if truncated else "",
        )
        return {
            "path": directory,
            "mode": "directory",
            "matches": matches,
            "match_count": len(matches),
            "truncated": truncated,
            "skipped_converted": state.skipped_converted,
            "extraction_method": "+".join(sorted(state.methods))
            if state.methods
            else "native_text",
        }

    @mcp.tool(title="Write Project File")
    def write_project_file(path: str, content: str) -> dict[str, str]:
        """Write or create a file in the project directory.

        Creates parent directories as needed. Only text file types are writable.

        Args:
            path: Relative file path, e.g. "agent-bus/src/new_module.py".
            content: Full file content to write.

        Returns:
            {"status": "written", "path": "<relative path>",
            "written_sha256": "<hex>"} — bare lowercase hex; callers compose
            ``sha256:`` / ``spec_sha256:`` prefixes as needed.
        """
        target = _safe_project_path(path)
        suffix = target.suffix.lower()
        if suffix and suffix not in _WRITABLE_SUFFIXES:
            return {"error": f"File type {suffix!r} is not writable"}
        if _is_binary(target):
            return {"error": f"Binary file type {suffix!r} cannot be written"}

        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            written_sha256 = durable_write_text(target, content)
            verify_persisted(target, written_sha256)
        except WriteVerifyError as exc:
            return cast("dict[str, str]", write_verify_error_dict(exc))
        rel = str(target.relative_to(_PROJECT_ROOT.resolve()))
        logger.info("write_project_file: %s (%d chars)", rel, len(content))
        record("mcp.project.file.written", path=rel, size=len(content))
        return {
            "status": "written",
            "path": rel,
            "written_sha256": written_sha256,
        }

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
            {"status": "edited: <operation>", "path": "<relative path>",
            "written_sha256": "<hex>"} — bare lowercase hex; callers compose
            ``sha256:`` / ``spec_sha256:`` prefixes as needed.
            For replace: includes "replacements_made".
        """
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

        try:
            result = perform_edit(
                target,
                operation,
                content,
                line=line if line > 0 else None,
                target_str=target_str if target_str else None,
                all_occurrences=all_occurrences,
            )
        except WriteVerifyError as exc:
            return cast("dict[str, str | int]", write_verify_error_dict(exc))
        rel = str(target.relative_to(_PROJECT_ROOT.resolve()))
        logger.info("edit_project_file: %s op=%s", rel, operation)
        record("mcp.project.file.edited", path=rel, operation=operation)
        return result
