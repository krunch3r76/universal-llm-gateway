"""Individual @mcp.tool registrations — thin wrappers calling impl functions.

Markdown section write ops (md_replace, md_append) are registered on the unified
``fs`` tool in server.py and delegate to the markdown overflow tool. Those ops
require heading-less content; redundant leading headings matching the target
section are stripped with normalized_heading: true in the response.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from mcp.types import ImageContent

from ._ops_image import view_image_impl
from ._ops_paths import (
    copy_file_impl,
    delete_file_impl,
    move_file_impl,
    remove_directory_impl,
)
from ._ops_text import edit_file_impl, list_files_impl, read_file_impl, write_file_impl

if TYPE_CHECKING:
    from fastmcp import FastMCP


def register_individual_tools(mcp: FastMCP) -> None:
    """Register individual named filesystem tools on *mcp*."""

    @mcp.tool(title="Write File")
    def write_file(
        path: str,
        content: str,
        expected_sha256: str = "",
        if_absent: bool = False,
    ) -> dict[str, Any]:
        """Write *content* to *path* inside the sandboxed files directory.

        Intermediate directories are created automatically.

        Optional CAS guards:
          expected_sha256 — overwrite only when the current file hash matches.
          if_absent — create-only; refuse when the path already exists.

        Args:
            path: Relative file path, e.g. "documents/resume.md".
            content: Text content to write.
            expected_sha256: Optional ``sha256:<hex>`` guard for safe overwrite.
            if_absent: When True, write only if the file does not exist.

        Returns:
            {"status": "written", "path": "<resolved path>"} on success, or a
            structured rejection dict with ``reason`` on guard failure.
        """
        return write_file_impl(
            path,
            content,
            expected_sha256=expected_sha256 or None,
            if_absent=if_absent,
        )

    @mcp.tool(title="Read File")
    def read_file(path: str, binary: bool = False) -> dict[str, Any]:
        """Read and return the contents of *path* from the sandboxed directory.

        Use the default text mode for markdown, notes, PDFs, and other supported
        document formats. Image files, archives, and other binary formats
        auto-route to base64 even without ``binary=True`` — reading a ``.png``,
        ``.jpg``, or archive returns ``content_base64`` with ``auto_binary: true``
        rather than corrupted text. Pass ``binary=True`` explicitly to force base64
        for any file type. Prefer ``view_image()`` for visual inspection.

        Args:
            path: Relative file path, e.g. "documents/notes.md".
            binary: If True, return base64-encoded bytes instead of decoded text.
                Image, audio, video, and archive files auto-route to binary even
                when False; magic-byte detection also covers files with absent or
                mismatched extensions.

        Returns:
            Text mode: {"content": "<file contents>", "path": "<resolved path>"}
            Binary mode: {"content_base64", "mime_type", "encoding", "bytes",
                "path", "auto_binary": true (when auto-routed)}

        Raises:
            FileNotFoundError: If the file does not exist.
            ValueError: If the path is outside the sandbox or invalid.
        """
        return read_file_impl(path, binary=binary)

    @mcp.tool(title="View Image")
    def view_image(
        path: str,
        max_dimension: int = 1024,
        quality: int = 60,
        mode: Literal["copy", "image"] = "copy",
    ) -> ImageContent | dict[str, str | int]:
        """View a photo or image from the sandbox filesystem.

        Resizes to a JPEG thumbnail. Prefer ``copy`` (default) when Claude or
        another client can open local files without bloating the MCP payload.
        Use ``image`` only when the response itself must carry inline pixels.

        Supported formats: .jpg, .jpeg, .png, .gif, .webp, .svg

        Args:
            path: Relative file path or `files://` URI, e.g. "dropbox/photo.jpg"
                  or "files://evidence/photo.jpg".
            max_dimension: Max width or height in pixels (default 1024).
            quality: JPEG compression quality 1-95 (default 60).
            mode: "copy" writes a JPEG thumbnail to the shared host-visible
                  image directory and returns its local path; "image" returns
                  inline ImageContent. Default "copy".

        Returns:
            Copy mode: {"path", "dimensions", "original", "bytes"}.
            Image mode: MCP ImageContent block (JPEG).

        Raises:
            FileNotFoundError: If the image file does not exist.
            ValueError: If the path is not a file, is outside the sandbox, or has an unsupported format.
        """
        return view_image_impl(
            path, max_dimension=max_dimension, quality=quality, mode=mode
        )

    @mcp.tool(title="Edit File")
    def edit_file(
        path: str,
        operation: str,
        content: str,
        line: int | None = None,
        target: str | None = None,
        all_occurrences: bool = False,
    ) -> dict[str, str | int]:
        """Atomically edit a text file in the sandboxed files directory.

        Performs a server-side read-modify-write so the model never needs
        to read the full file content just to prepend or append.

        Editing is limited to plain-text formats; binary formats (.docx, .pdf, etc.)
        must be written in full via write_file().

        Args:
            path: Relative file path (e.g. "notes/daily.md").
            operation: One of:
                - "prepend": Insert content at the beginning.
                - "append": Insert content at the end.
                - "insert_at_line": Insert at a 1-indexed line number
                  (requires `line` argument).
                - "replace": Replace occurrences of `target` string
                  (requires `target` argument).
            content: Text to insert or use as replacement.
            line: 1-indexed line number for insert_at_line.
            target: String to find for replace.
            all_occurrences: If True, replace all occurrences (default: first only).

        Returns:
            {"status": "edited: <op>", "path": "..."}
            For replace: includes "replacements_made".
        """
        return edit_file_impl(
            path,
            operation,
            content,
            line=line,
            target=target,
            all_occurrences=all_occurrences,
        )

    @mcp.tool(title="Move File")
    def move_file(source: str, destination: str) -> dict[str, str]:
        """Move or rename a file within the sandbox.

        Works with any file type including images and binary files.
        Creates intermediate directories at the destination automatically.
        Overwrites the destination if it already exists.

        Args:
            source: Current relative path, e.g. "dropbox/photo.jpg".
            destination: New relative path, e.g. "notes/legal/assets/photo.jpg".

        Returns:
            {"status": "moved", "from": "<old path>", "to": "<new path>"}

        Raises:
            FileNotFoundError: If the source file does not exist.
            ValueError: If the source path is not a file or paths are outside the sandbox.
        """
        return move_file_impl(source, destination)

    @mcp.tool(title="Copy File")
    def copy_file(source: str, destination: str) -> dict[str, str]:
        """Copy a file within the sandbox.

        Works with any file type including images and binary files.
        Creates intermediate directories at the destination automatically.
        Overwrites the destination if it already exists.

        Args:
            source: Relative path to copy from, e.g. "dropbox/photo.jpg".
            destination: Relative path to copy to, e.g. "archive/photo.jpg".

        Returns:
            {"status": "copied", "from": "<source path>", "to": "<new path>"}

        Raises:
            FileNotFoundError: If the source file does not exist.
            ValueError: If the source path is not a file or paths are outside the sandbox.
        """
        return copy_file_impl(source, destination)

    @mcp.tool(title="Remove Directory")
    def remove_directory(directory: str) -> dict[str, str]:
        """Remove a directory and all its contents from the sandbox.

        Deletes the directory and everything inside it recursively.
        Use with care — this is irreversible.

        Args:
            directory: Relative directory path, e.g. "notes/old-drafts".

        Returns:
            {"status": "removed", "path": "<resolved path>"}

        Raises:
            FileNotFoundError: If the directory does not exist.
            ValueError: If the path is not a directory, is outside the sandbox, or attempts to remove the sandbox root.
        """
        return remove_directory_impl(directory)

    @mcp.tool(title="Delete File")
    def delete_file(path: str) -> dict[str, str]:
        """Soft-delete a file by moving it to the sandbox trash/ directory.

        The file is preserved at trash/<original-path> so it can be inspected
        or restored.  Name conflicts are resolved by appending a zero-padded
        numeric suffix to the stem: report-01.md, report-02.md, etc.

        Only individual files may be deleted — directories are rejected.
        Files already inside trash/ are rejected (use remove_directory to purge).

        Args:
            path: Relative file path, e.g. "documents/draft.md".

        Returns:
            {"status": "trashed", "path": "<original resolved path>",
             "trash_path": "<trash destination>"}

        Raises:
            FileNotFoundError: If the file does not exist.
            ValueError: If the path is not a file, is outside the sandbox,
                        or is already inside trash/.
        """
        return delete_file_impl(path)

    @mcp.tool(title="List Files")
    def list_files(directory: str = "") -> dict[str, list[str]]:
        """List files in *directory* within the sandboxed files directory.

        Args:
            directory: Relative directory path. Empty string lists the root.

        Returns:
            {"files": ["<relative paths>", ...]}
        """
        return list_files_impl(directory)
