"""Filesystem tools — sandboxed read/write/list in /data/files.

All paths are resolved relative to _SANDBOX_ROOT. Traversal attempts
(../) are rejected before resolution so that the container volume mount
is complemented by explicit code-level defense in depth.

Supported read formats: .md, .txt, .docx, .odt, .eml, .pdf, .html, .json, .yaml.
Supported write formats: .md, .txt, .docx, .pdf, .yaml, .yml.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from mcp_events import record

from .file_editor import perform_edit

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = logging.getLogger(__name__)

_SANDBOX_ROOT = Path("/data/files")
_ALLOWED_WRITE_SUFFIXES = {".md", ".txt", ".docx", ".pdf", ".yaml", ".yml", ".py"}
_ALLOWED_READ_SUFFIXES = {
    ".md",
    ".txt",
    ".docx",
    ".odt",
    ".eml",
    ".pdf",
    ".doc",
    ".html",
    ".json",
    ".yaml",
    ".py",
}
_ALLOWED_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg"}
_EDITABLE_SUFFIXES = {".md", ".txt"}


def _read_plain(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _read_docx(path: Path) -> str:
    from docx import Document  # type: ignore[import-untyped]

    doc = Document(str(path))
    return "\n".join(para.text for para in doc.paragraphs)


def _read_odt(path: Path) -> str:
    from odf import teletype  # type: ignore[import-untyped]
    from odf.opendocument import load as odf_load  # type: ignore[import-untyped]
    from odf.text import P  # type: ignore[import-untyped]

    doc = odf_load(str(path))
    return "\n".join(teletype.extractText(node) for node in doc.getElementsByType(P))


def _read_pdf(path: Path) -> str:
    import pymupdf4llm  # type: ignore[import-untyped]

    return pymupdf4llm.to_markdown(str(path))


def _read_eml(path: Path) -> str:
    import email
    import email.policy

    with path.open("rb") as f:
        msg = email.message_from_binary_file(f, policy=email.policy.default)

    lines: list[str] = []

    # Headers as frontmatter
    lines.append("---")
    for header in ("from", "to", "cc", "subject", "date", "message-id"):
        val = msg.get(header, "")
        if val:
            lines.append(f"{header}: {val}")
    lines.append("---")
    lines.append("")

    # Body — prefer text/plain, fall back to text/html via html2text
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            cd = str(part.get("Content-Disposition", ""))
            if ct == "text/plain" and "attachment" not in cd:
                body = part.get_content()
                break
        if not body:
            for part in msg.walk():
                ct = part.get_content_type()
                cd = str(part.get("Content-Disposition", ""))
                if ct == "text/html" and "attachment" not in cd:
                    import html2text  # type: ignore[import-untyped]

                    body = html2text.html2text(part.get_content())
                    break
    else:
        body = msg.get_content()

    lines.append(body.strip())

    # Inline PDF attachments → extract as markdown sections
    for part in msg.walk():
        filename = part.get_filename()
        if not filename:
            continue
        lines.append("")
        lines.append("---")
        lines.append(f"## Attachment: {filename}")
        lines.append("")
        ct = part.get_content_type()
        if ct == "application/pdf":
            import pymupdf  # type: ignore[import-untyped]
            import pymupdf4llm  # type: ignore[import-untyped]

            data = part.get_payload(decode=True)
            doc = pymupdf.open(stream=data, filetype="pdf")
            text = pymupdf4llm.to_markdown(doc)
            doc.close()
            lines.append(text.strip())
        else:
            lines.append(f"[{ct} — not extracted]")

    return "\n".join(lines)


def _safe_path(relative: str) -> Path:
    """Resolve *relative* inside the sandbox, rejecting traversal attempts.

    Raises ValueError if the resolved path escapes the sandbox root.
    """
    # Strip leading slash so callers can use either form
    clean = relative.lstrip("/")
    candidate = (_SANDBOX_ROOT / clean).resolve()
    try:
        candidate.relative_to(_SANDBOX_ROOT.resolve())
    except ValueError:
        raise ValueError(
            f"Path {relative!r} resolves outside sandbox; traversal rejected"
        )
    return candidate


def _write_plain(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _write_docx(path: Path, content: str) -> None:
    from docx import Document  # type: ignore[import-untyped]

    path.parent.mkdir(parents=True, exist_ok=True)
    doc = Document()
    for para in content.split("\n"):
        doc.add_paragraph(para)
    doc.save(str(path))


def _write_pdf(path: Path, content: str) -> None:
    from fpdf import FPDF  # type: ignore[import-untyped]

    path.parent.mkdir(parents=True, exist_ok=True)
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=11)
    for line in content.split("\n"):
        # multi_cell wraps long lines; empty string produces blank line
        pdf.multi_cell(0, 6, txt=line or " ")
    pdf.output(str(path))


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
        """
        src = _safe_path(path)
        if not src.exists():
            raise FileNotFoundError(f"File not found: {path!r}")
        if not src.is_file():
            raise ValueError(f"Path is not a file: {path!r}")

        suffix = src.suffix.lower()
        if suffix not in _ALLOWED_READ_SUFFIXES:
            raise ValueError(
                f"Unsupported format {suffix!r} for reading. "
                f"Allowed: {', '.join(sorted(_ALLOWED_READ_SUFFIXES))}"
            )

        read_handlers = {
            ".docx": _read_docx,
            ".odt": _read_odt,
            ".eml": _read_eml,
            ".pdf": _read_pdf,
        }
        read_handler = read_handlers.get(suffix, _read_plain)
        content = read_handler(src)

        record("mcp.tool.file.read", path=path, resolved=str(src), chars=len(content))
        logger.debug("read_file: read %s (%d chars)", src, len(content))
        return {"content": content, "path": str(src)}

    @mcp.tool()
    def view_image(
        path: str, max_dimension: int = 1024, quality: int = 60
    ) -> dict[str, str]:
        """View a photo or image from the sandbox filesystem.

        Resizes and compresses the image to a JPEG thumbnail saved at
        .thumbnails/<name>.jpg inside the sandbox. Returns the thumbnail
        path and metadata — no base64 in the response, so it won't
        overflow the context window.

        To actually see the image, read the thumbnail file with your
        file viewer or bash tool.

        Supported formats: .jpg, .jpeg, .png, .gif, .webp

        Args:
            path: Relative file path, e.g. "dropbox/photo.jpg".
            max_dimension: Max width or height in pixels (default 1024).
            quality: JPEG compression quality 1-95 (default 60).

        Returns:
            {"thumbnail": "<path to compressed JPEG>", "original": "<source path>",
             "original_size": "WxH", "thumbnail_size": "WxH", "bytes": "<file size>"}
        """
        from PIL import Image as PILImage

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

        img = PILImage.open(src)
        original_size = f"{img.width}x{img.height}"

        img.thumbnail((max_dimension, max_dimension), PILImage.Resampling.LANCZOS)
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")

        thumb_dir = _SANDBOX_ROOT / ".thumbnails"
        thumb_dir.mkdir(parents=True, exist_ok=True)
        thumb_name = src.stem + ".jpg"
        thumb_path = thumb_dir / thumb_name
        img.save(str(thumb_path), format="JPEG", quality=quality, optimize=True)

        thumb_size = f"{img.width}x{img.height}"
        file_bytes = thumb_path.stat().st_size
        thumb_rel = str(thumb_path.relative_to(_SANDBOX_ROOT))

        record(
            "mcp.tool.image.viewed",
            path=path,
            resolved=str(src),
            original=original_size,
            thumbnail=thumb_rel,
            thumbnail_size=thumb_size,
            bytes=file_bytes,
        )
        logger.info(
            "view_image: %s %s -> %s (%d bytes)",
            src,
            original_size,
            thumb_size,
            file_bytes,
        )
        return {
            "thumbnail": thumb_rel,
            "original": path,
            "original_size": original_size,
            "thumbnail_size": thumb_size,
            "bytes": str(file_bytes),
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

        files = sorted(
            str(p.relative_to(_SANDBOX_ROOT))
            for p in target.rglob("*")
            if p.is_file() and not p.is_relative_to(_SANDBOX_ROOT / ".thumbnails")
        )
        record("mcp.tool.file.listed", directory=directory or ".", count=len(files))
        logger.debug("list_files: %s → %d files", target, len(files))
        return {"files": files}
