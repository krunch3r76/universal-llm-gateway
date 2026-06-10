"""Unified `files` MCP tool dispatcher for the cortex sandbox."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ._fs_dispatch import sandbox_op_names
from ._ops_binary import append_binary_impl, write_binary_impl
from ._ops_paths import copy_file_impl, delete_file_impl, move_file_impl
from ._ops_search import search_path_impl
from ._ops_text import (
    edit_file_impl,
    list_files_impl,
    read_file_impl,
    read_files_batch_impl,
    write_file_impl,
)
from ._paths import DROPBOX_COPY_WARNING, DROPBOX_READ_HINT, FS_WORKFLOW_HINTS

if TYPE_CHECKING:
    from fastmcp import FastMCP


def register_files_tool(mcp: FastMCP) -> None:
    """Register the unified `files` tool on *mcp*."""

    @mcp.tool(title="Files (Unified Sandbox)")
    def files(
        op: str = "",
        path: str = "",
        paths: list[str] = [],  # noqa: B006 — Pydantic handles mutable default
        content: str = "",
        target: str = "",
        line: int = 0,
        all_occurrences: bool = False,
        binary: bool = False,
        offset: int = 0,
        limit: int = 0,
        expected_sha256: str = "",
        if_absent: bool = False,
    ) -> dict[str, Any]:
        """Unified file operations for the sandboxed /data/files directory.

        Use `files` for persistent user documents, notes, uploads, exports,
        and any files shared with the agent (dropbox). For repository source code,
        config, tasks, docs, and scripts use the workspaces sandbox via `fs`.

        For large markdown documents (>5k chars), prefer the `markdown` tool
        (with sandbox="cortex") which provides section-level read/write/delete
        without ingesting the entire file.

        Text-format conversion: PDF, DOCX, ODT, EML, and HTML files are
        automatically converted to readable text/markdown in `read` mode — no
        `binary=True` needed. HTML returns stripped markdown prose via html2text.
        Image files, archives, and other binary formats auto-route to base64
        even without `binary=True` — reading a `.png`, `.jpg`, or archive returns
        `{content_base64, auto_binary: true}` rather than corrupted text. Pass
        `binary=True` explicitly to force base64 for any file type. Prefer
        `view_image` when the task is visual inspection rather than moving bytes onward.

        Ops:
          read   — read file contents (path required; optional offset/limit
              for line-range slice — 0-based offset, max lines in limit)
          read_multi — batch read multiple files (paths required)
          write  — create/overwrite file (path, content required; optional
              expected_sha256 for concurrent-safe overwrite, if_absent for
              create-only — see friction-13695 sidecar)
          write_binary — write base64-encoded binary data (path required,
              content = base64 string). Use to stage PDFs, images, or other
              binary files for downstream tools like extract_document.
          append_binary — append base64-encoded bytes to a file (path required,
              content = base64 chunk). For chunked upload of large binaries:
              first call write_binary with chunk 1, then append_binary for
              each subsequent chunk. Each chunk must be independently valid
              base64 (padded). Creates the file if it doesn't exist.
          append — append to end of file (path, content required)
          prepend — insert at beginning of file (path, content required)
          replace — find-and-replace in file (path, target required; content = replacement)
          insert_at_line — insert at line N (path, content, line required)
          list   — list files in directory (path optional, defaults to root)
          search — regex-search a file or directory (path required, content =
              regex pattern). Searches text and converted documents
              (PDF/DOCX/ODT/EML/HTML), sidecar-first for PDFs. File mode returns
              {path, mode: "file", matches: [{line, text}], truncated,
              extraction_method}; directory mode adds a "file" key per match and
              a "skipped_converted" count (converted-file extraction is cost-bounded).
          move   — move or rename a file (path = source, target = destination)
          copy   — copy a file (path = source, target = destination)
          delete — soft-delete a file to trash/ (path required); name conflicts
              resolved as <stem>-01.<ext>, <stem>-02.<ext>, etc. Restore via
              move(path='trash/<original>', target='<original>').

        read_multi — batch read multiple files (paths required)
            Use when loading multiple related files such as boot sequence prompts
            or config + schema pairs. One call replaces N reads. Returns
            {path: content} or {path: {error: msg}} for missing files.

        Workflow chains (write, write_binary, move, copy responses carry a ``_next`` hint):
          move   → cortex entity_update/assert (update source_uri to permanent path)
          copy   → cortex entity_create or assert (register the copy as a new document if needed)
          write  → cortex entity_create or assert (register the new document)
          write_binary → cortex entity_create or assert; or extract_document for PDFs/images

        Args:
            op: Operation name (see above).
            path: Relative file path, e.g. "documents/resume.md".
            paths: Relative file paths for read_multi.
            content: Text content for write/edit ops (replacement text for replace).
            target: String to find — required for replace.
            line: 1-indexed line number — required for insert_at_line.
            all_occurrences: For replace: replace all matches (default false).
            binary: For read/read_multi, return base64 bytes instead of decoded text.
            offset: For read, 0-based line offset to skip (default 0 = from start).
            limit: For read, max lines to return (default 0 = no cap).

        Returns:
            Operation-dependent result dict.
        """
        if not op:
            raise ValueError("'op' is required")
        if op == "read":
            if not path:
                raise ValueError("'path' is required for read")
            result = read_file_impl(path, binary=binary, offset=offset, limit=limit)
            if path.startswith("dropbox/"):
                result["_next"] = DROPBOX_READ_HINT
            return result
        if op == "read_multi":
            if not paths:
                raise ValueError("'paths' is required for read_multi")
            return read_files_batch_impl(paths, binary=binary)
        if op == "write":
            if not path:
                raise ValueError("'path' is required for write")
            if not content:
                raise ValueError("'content' is required for write")
            result = write_file_impl(
                path,
                content,
                expected_sha256=expected_sha256 or None,
                if_absent=if_absent,
            )
            result["_next"] = FS_WORKFLOW_HINTS["write"]
            return result
        if op == "write_binary":
            if not path:
                raise ValueError("'path' is required for write_binary")
            if not content:
                raise ValueError("'content' (base64) is required for write_binary")
            result = write_binary_impl(path, content)
            result["_next"] = FS_WORKFLOW_HINTS["write_binary"]
            return result
        if op == "append_binary":
            if not path:
                raise ValueError("'path' is required for append_binary")
            if not content:
                raise ValueError("'content' (base64) is required for append_binary")
            return append_binary_impl(path, content)
        if op == "list":
            return list_files_impl(path)
        if op == "search":
            if not path:
                raise ValueError("'path' is required for search")
            if not content:
                raise ValueError(
                    "'content' is required for search and holds the regex "
                    "pattern. Example: fs(sandbox='cortex', op='search', "
                    "path='notes/paper.pdf', content='credibility')"
                )
            return search_path_impl(path, content)
        if op in ("append", "prepend"):
            if not path:
                raise ValueError(f"'path' is required for {op}")
            if not content:
                raise ValueError(f"'content' is required for {op}")
            return edit_file_impl(path, op, content)
        if op == "replace":
            if not path:
                raise ValueError("'path' is required for replace")
            if not target:
                raise ValueError("'target' is required for replace")
            return edit_file_impl(
                path,
                "replace",
                content,
                target=target,
                all_occurrences=all_occurrences,
            )
        if op == "insert_at_line":
            if not path:
                raise ValueError("'path' is required for insert_at_line")
            if not line:
                raise ValueError("'line' is required for insert_at_line")
            return edit_file_impl(path, "insert_at_line", content, line=line)
        if op == "move":
            if not path:
                raise ValueError("'path' is required for move")
            if not target:
                raise ValueError("'target' is required for move")
            result = move_file_impl(path, target)
            result["_next"] = FS_WORKFLOW_HINTS["move"]
            return result
        if op == "copy":
            if not path:
                raise ValueError("'path' is required for copy")
            if not target:
                raise ValueError("'target' is required for copy")
            result = copy_file_impl(path, target)
            result["_next"] = FS_WORKFLOW_HINTS["copy"]
            if path.startswith("dropbox/"):
                result["_warning"] = DROPBOX_COPY_WARNING
            return result
        if op == "delete":
            if not path:
                raise ValueError("'path' is required for delete")
            result = delete_file_impl(path)
            result["_next"] = FS_WORKFLOW_HINTS["delete"]
            return result
        raise ValueError(f"Unknown op: {op!r}. Use: {sandbox_op_names('cortex')}")
