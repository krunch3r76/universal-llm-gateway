"""Filesystem tools — sandboxed read/write/list in /data/files.

All paths are resolved relative to _SANDBOX_ROOT. Traversal attempts
(../) are rejected before resolution so that the container volume mount
is complemented by explicit code-level defense in depth.

Supported read formats: .md, .txt, .docx, .odt, .eml, .pdf, .html, .json, .yaml.
Supported write formats: .md, .txt, .docx, .pdf, .yaml, .yml.
"""

from __future__ import annotations

import base64
import hashlib
import io
import logging
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from mcp.types import ImageContent
from mcp_events import record

from ._file_helpers import read_file_result, read_files_batch
from .file_editor import perform_edit

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = logging.getLogger(__name__)

_SANDBOX_ROOT = Path("/data/files")
_SHARED_IMAGE_DIR = Path(
    os.environ.get("MCP_SHARED_IMAGE_DIR", str(_SANDBOX_ROOT / ".shared-images"))
)
_SHARED_IMAGE_HOST_ROOT = Path(
    os.environ.get("MCP_SHARED_IMAGE_HOST_ROOT", str(_SHARED_IMAGE_DIR))
)
_ALLOWED_WRITE_SUFFIXES = {".md", ".txt", ".docx", ".pdf", ".yaml", ".yml", ".py"}
_ALLOWED_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg"}
_EDITABLE_SUFFIXES = {".md", ".txt"}


def _safe_path(relative: str) -> Path:
    """Resolve *relative* inside the sandbox, rejecting traversal attempts.

    Raises ValueError if the resolved path escapes the sandbox root.
    """
    # Strip leading slash so callers can use either form
    clean = relative.lstrip("/")
    candidate = (_SANDBOX_ROOT / clean).resolve()
    try:
        candidate.relative_to(_SANDBOX_ROOT)
    except ValueError:
        raise ValueError(
            f"Path {relative!r} resolves outside sandbox; traversal rejected"
        )
    return candidate


def _write_plain(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _write_docx(path: Path, content: str) -> None:
    from docx import Document

    path.parent.mkdir(parents=True, exist_ok=True)
    doc = Document()
    for para in content.split("\n"):
        doc.add_paragraph(para)
    doc.save(str(path))


def _write_pdf(path: Path, content: str) -> None:
    from fpdf import FPDF

    path.parent.mkdir(parents=True, exist_ok=True)
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=11)
    for line in content.split("\n"):
        # multi_cell wraps long lines; empty string produces blank line
        pdf.multi_cell(0, 6, txt=line or " ")
    pdf.output(str(path))


def _render_thumbnail_bytes(
    src: Path, *, max_dimension: int, quality: int
) -> tuple[bytes, str, str]:
    from PIL import Image as PILImage

    with PILImage.open(src) as opened:
        original_size = f"{opened.width}x{opened.height}"
        opened.thumbnail((max_dimension, max_dimension), PILImage.Resampling.LANCZOS)
        if opened.mode in ("RGBA", "P"):
            rendered = opened.convert("RGB")
        else:
            rendered = opened

    thumb_size = f"{rendered.width}x{rendered.height}"
    buffer = io.BytesIO()
    rendered.save(buffer, format="JPEG", quality=quality, optimize=True)
    return buffer.getvalue(), original_size, thumb_size


def _shared_image_name(src: Path, *, max_dimension: int, quality: int) -> str:
    safe_stem = re.sub(r"[^A-Za-z0-9._-]+", "-", src.stem).strip("-.") or "image"
    rel_path = src.relative_to(_SANDBOX_ROOT).as_posix()
    stat = src.stat()
    fingerprint = hashlib.sha256(
        f"{rel_path}:{stat.st_mtime_ns}:{stat.st_size}:{max_dimension}:{quality}".encode()
    ).hexdigest()[:16]
    return f"{safe_stem}-{fingerprint}.jpg"


def _write_shared_image(filename: str, jpeg_bytes: bytes) -> tuple[Path, Path]:
    _SHARED_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    shared_path = _SHARED_IMAGE_DIR / filename
    shared_path.write_bytes(jpeg_bytes)
    return shared_path, _SHARED_IMAGE_HOST_ROOT / filename


def register_filesystem_tools(mcp: FastMCP) -> None:
    """Register all filesystem tools on *mcp*."""

    @mcp.tool()
    def write_file(path: str, content: str) -> dict[str, str]:
        """Write *content* to *path* inside the sandboxed files directory.

        Supported extensions: .md, .txt, .docx, .pdf, .yaml, .yml.
        Intermediate directories are created automatically.

        Args:
            path: Relative file path, e.g. "documents/resume.md".
            content: Text content to write.

        Returns:
            {"status": "written", "path": "<resolved path>"}
        """
        dest = _safe_path(path)
        suffix = dest.suffix.lower()
        if suffix not in _ALLOWED_WRITE_SUFFIXES:
            record(
                "mcp.tool.file.write_failed",
                path=path,
                reason="unsupported_format",
                suffix=suffix,
            )
            raise ValueError(
                f"Unsupported format {suffix!r}. "
                f"Allowed: {', '.join(sorted(_ALLOWED_WRITE_SUFFIXES))}"
            )
        try:
            write_handlers = {
                ".docx": _write_docx,
                ".pdf": _write_pdf,
            }
            write_handler = write_handlers.get(suffix, _write_plain)
            write_handler(dest, content)
        except OSError as exc:
            record(
                "mcp.tool.file.write_failed",
                path=path,
                resolved=str(dest),
                reason="os_error",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            logger.exception(
                "write_file: OS error writing %s", dest
            )  # Use logger.exception to include traceback
            raise

        record(
            "mcp.tool.file.written", path=path, resolved=str(dest), chars=len(content)
        )
        logger.debug("write_file: wrote %s (%d chars)", dest, len(content))
        return {"status": "written", "path": str(dest)}

    @mcp.tool()
    def read_file(path: str) -> dict[str, str]:
        """Read and return the contents of *path* from the sandboxed directory.

        Supported formats: .md, .txt, .docx, .odt, .eml, .pdf, .html, .json, .yaml

        Args:
            path: Relative file path, e.g. "documents/notes.md".

        Returns:
            {"content": "<file contents>", "path": "<resolved path>"}

        Raises:
            FileNotFoundError: If the file does not exist.
            ValueError: If the path is outside the sandbox or invalid.
        """
        result = read_file_result(path)
        record(
            "mcp.tool.file.read",
            path=path,
            resolved=result["path"],
            chars=len(result["content"]),
        )
        logger.debug(
            "read_file: read %s (%d chars)", result["path"], len(result["content"])
        )
        return result

    @mcp.tool()
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
            path: Relative file path, e.g. "dropbox/photo.jpg".
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
        src = _safe_path(path)
        if not src.exists():
            raise FileNotFoundError(f"Image not found: {path!r}")
        if not src.is_file():
            raise ValueError(f"Path is not a file: {path!r}")

        suffix = src.suffix.lower()
        if suffix not in _ALLOWED_IMAGE_SUFFIXES:
            raise ValueError(
                f"Unsupported image format {suffix!r}. "
                f"Allowed: {', '.join(sorted(_ALLOWED_IMAGE_SUFFIXES))}"
            )

        jpeg_bytes, original_size, thumb_size = _render_thumbnail_bytes(
            src,
            max_dimension=max_dimension,
            quality=quality,
        )

        record(
            "mcp.tool.image.viewed",
            path=path,
            resolved=str(src),
            original=original_size,
            thumbnail_size=thumb_size,
            bytes=len(jpeg_bytes),
            mode=mode,
        )
        logger.info(
            "view_image: %s %s -> %s (%d bytes, mode=%s)",
            src,
            original_size,
            thumb_size,
            len(jpeg_bytes),
            mode,
        )

        if mode == "image":
            return ImageContent(
                type="image",
                data=base64.b64encode(jpeg_bytes).decode(),
                mimeType="image/jpeg",
            )

        shared_name = _shared_image_name(
            src,
            max_dimension=max_dimension,
            quality=quality,
        )
        shared_path, shared_host_path = _write_shared_image(shared_name, jpeg_bytes)
        logger.info("view_image copy: %s", shared_path)
        return {
            "path": str(shared_host_path),
            "dimensions": thumb_size,
            "original": original_size,
            "bytes": len(jpeg_bytes),
        }

    @mcp.tool()
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

        Allowed extensions: .md, .txt

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
        dest = _safe_path(path)
        if dest.suffix.lower() not in _EDITABLE_SUFFIXES:
            raise ValueError(
                f"Unsupported format {dest.suffix!r} for editing. Allowed: "
                + ", ".join(sorted(_EDITABLE_SUFFIXES))
            )

        try:
            result = perform_edit(
                path=dest,
                operation=operation,
                content=content,
                line=line,
                target_str=target,
                all_occurrences=all_occurrences,
            )
            event_payload: dict[str, str | int | bool] = {
                "sandbox": "files",
                "path": path,
                "operation": operation,
                "content_chars": len(content),
            }
            if line is not None:
                event_payload["line"] = line
            if target is not None:
                event_payload["target_chars"] = len(target)
            if operation == "replace":
                event_payload["all_occurrences"] = all_occurrences
                event_payload["replacements_made"] = result.get("replacements_made", 0)
            record("mcp.tool.file.edited", **event_payload)
            logger.info("edit_file: %s on %s", operation, path)
            return result
        except (FileNotFoundError, ValueError) as exc:
            reason = (
                "not_found"
                if isinstance(exc, FileNotFoundError)
                else "validation_error"
            )
            record(
                "mcp.tool.file.edit_failed",
                sandbox="files",
                path=path,
                operation=operation,
                reason=reason,
                error_message=str(exc),
            )
            logger.exception("edit_file failed on %s", path)
            raise

    @mcp.tool()
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
        import shutil

        src = _safe_path(source)
        dst = _safe_path(destination)
        if not src.exists():
            raise FileNotFoundError(f"Source not found: {source!r}")
        if not src.is_file():
            raise ValueError(f"Source is not a file: {source!r}")

        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        record("mcp.tool.file.moved", source=source, destination=destination)
        logger.info("move_file: %s → %s", src, dst)
        return {"status": "moved", "from": str(src), "to": str(dst)}

    @mcp.tool()
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
        import shutil

        src = _safe_path(source)
        dst = _safe_path(destination)
        if not src.exists():
            raise FileNotFoundError(f"Source not found: {source!r}")
        if not src.is_file():
            raise ValueError(f"Source is not a file: {source!r}")

        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(src), str(dst))
        record("mcp.tool.file.copied", source=source, destination=destination)
        logger.info("copy_file: %s → %s", src, dst)
        return {"status": "copied", "from": str(src), "to": str(dst)}

    @mcp.tool()
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
        import shutil

        target = _safe_path(directory)
        if not target.exists():
            raise FileNotFoundError(f"Directory not found: {directory!r}")
        if not target.is_dir():
            raise ValueError(f"Path is not a directory: {directory!r}")
        if target == _SANDBOX_ROOT:
            raise ValueError("Cannot remove the sandbox root directory")

        shutil.rmtree(str(target))
        record("mcp.tool.dir.removed", directory=directory)
        logger.info("remove_directory: %s", target)
        return {"status": "removed", "path": str(target)}

    @mcp.tool()
    def delete_file(path: str) -> dict[str, str]:
        """Delete a file from the sandboxed files directory.

        Only individual files may be deleted — directories are rejected.

        Args:
            path: Relative file path, e.g. "documents/draft.md".

        Returns:
            {"status": "deleted", "path": "<resolved path>"}

        Raises:
            FileNotFoundError: If the file does not exist.
            ValueError: If the path is not a file or is outside the sandbox.
        """
        target = _safe_path(path)
        if not target.exists():
            raise FileNotFoundError(f"File not found: {path!r}")
        if not target.is_file():
            raise ValueError(
                f"Path is not a file (directories cannot be deleted): {path!r}"
            )

        target.unlink()
        record("mcp.tool.file.deleted", sandbox="files", path=path)
        logger.info("delete_file: deleted %s", target)
        return {"status": "deleted", "path": str(target)}

    @mcp.tool()
    def list_files(directory: str = "") -> dict[str, list[str]]:
        """List files in *directory* within the sandboxed files directory.

        Args:
            directory: Relative directory path. Empty string lists the root.

        Returns:
            {"files": ["<relative paths>", ...]}
        """
        target = _safe_path(directory) if directory else _SANDBOX_ROOT
        if not target.exists():
            return {"files": []}
        if not target.is_dir():
            raise ValueError(f"Path is not a directory: {directory!r}")

        generated_dir = _SHARED_IMAGE_DIR.resolve()
        files = sorted(
            str(p.relative_to(_SANDBOX_ROOT))
            for p in target.rglob("*")
            if p.is_file() and not p.is_relative_to(generated_dir)
        )
        record("mcp.tool.file.listed", directory=directory or ".", count=len(files))
        logger.debug("list_files: %s → %d files", target, len(files))
        return {"files": files}

    @mcp.tool()
    def files(
        op: str = "",
        path: str = "",
        paths: list[str] = [],  # noqa: B006 — Pydantic handles mutable default
        content: str = "",
        target: str = "",
        line: int = 0,
        all_occurrences: bool = False,
    ) -> dict[str, Any]:
        """Unified file operations for the sandboxed /data/files directory.

        Use `files` for persistent user documents, notes, uploads, and exports
        under /data/files. For repository source code use `project`; for
        workspace scratchpads, discoveries, and specs under tasks/ use `context`.

        Ops:
          read   — read file contents (path required)
          read_multi — batch read multiple files (paths required)
          write  — create/overwrite file (path, content required)
          append — append to end of file (path, content required)
          prepend — insert at beginning of file (path, content required)
          replace — find-and-replace in file (path, target required; content = replacement)
          insert_at_line — insert at line N (path, content, line required)
          list   — list files in directory (path optional, defaults to root)

        read_multi — batch read multiple files (paths required)
            Use when loading multiple related files such as boot sequence prompts
            or config + schema pairs. One call replaces N reads. Returns
            {path: content} or {path: {error: msg}} for missing files.

        Args:
            op: Operation name (see above).
            path: Relative file path, e.g. "documents/resume.md".
            paths: Relative file paths for read_multi.
            content: Text content for write/edit ops (replacement text for replace).
            target: String to find — required for replace.
            line: 1-indexed line number — required for insert_at_line.
            all_occurrences: For replace: replace all matches (default false).

        Returns:
            Operation-dependent result dict.
        """
        if not op:
            raise ValueError("'op' is required")
        if op == "read":
            if not path:
                raise ValueError("'path' is required for read")
            return read_file(path)
        if op == "read_multi":
            if not paths:
                raise ValueError("'paths' is required for read_multi")
            results = read_files_batch(paths)
            for batch_path, batch_result in results.items():
                if isinstance(batch_result, str):
                    record(
                        "mcp.tool.file.read",
                        path=batch_path,
                        resolved=str(_safe_path(batch_path)),
                        chars=len(batch_result),
                        batched=True,
                    )
            logger.debug("files: batch read %d file(s)", len(paths))
            return {"files": results}
        if op == "write":
            if not path:
                raise ValueError("'path' is required for write")
            if not content:
                raise ValueError("'content' is required for write")
            return write_file(path, content)
        if op == "list":
            return list_files(path)
        if op in ("append", "prepend"):
            if not path:
                raise ValueError(f"'path' is required for {op}")
            if not content:
                raise ValueError(f"'content' is required for {op}")
            return edit_file(path, op, content)
        if op == "replace":
            if not path:
                raise ValueError("'path' is required for replace")
            if not target:
                raise ValueError("'target' is required for replace")
            return edit_file(
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
            return edit_file(path, "insert_at_line", content, line=line)
        raise ValueError(
            f"Unknown op: {op!r}. "
            "Use: read, read_multi, write, append, prepend, replace, insert_at_line, list"
        )
