"""Filesystem tools — sandboxed read/write/list in /data/files.

All paths are resolved relative to _SANDBOX_ROOT. Traversal attempts
(../) are rejected before resolution so that the container volume mount
is complemented by explicit code-level defense in depth.

Supported read formats: .md, .txt (plain), .docx (python-docx), .odt (odfpy).
Supported write formats: .md, .txt (plain), .docx (python-docx), .pdf (fpdf2).
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
_ALLOWED_WRITE_SUFFIXES = {".md", ".txt", ".docx", ".pdf"}
_ALLOWED_READ_SUFFIXES = {".md", ".txt", ".docx", ".odt"}
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
    return "\n".join(
        teletype.extractText(node)
        for node in doc.getElementsByType(P)
    )


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

        Supported extensions: .md, .txt, .docx, .pdf.
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
            raise ValueError(
                f"Unsupported format {suffix!r}. "
                f"Allowed: {', '.join(sorted(_ALLOWED_WRITE_SUFFIXES))}"
            )
        match suffix:
            case ".docx":
                _write_docx(dest, content)
            case ".pdf":
                _write_pdf(dest, content)
            case _:
                _write_plain(dest, content)

        logger.info("write_file: wrote %s (%d chars)", dest, len(content))
        return {"status": "written", "path": str(dest)}

    @mcp.tool()
    def read_file(path: str) -> dict[str, str]:
        """Read and return the contents of *path* from the sandboxed directory.

        Supported formats: .md, .txt (plain text), .docx (Word), .odt (OpenDocument).

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

        match suffix:
            case ".docx":
                content = _read_docx(src)
            case ".odt":
                content = _read_odt(src)
            case _:
                content = _read_plain(src)

        logger.info("read_file: read %s (%d chars)", src, len(content))
        return {"content": content, "path": str(src)}

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
            logger.warning("edit_file failed on %s: %s", path, exc)
            raise

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
            raise ValueError(f"Path is not a file (directories cannot be deleted): {path!r}")

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
            str(p.relative_to(_SANDBOX_ROOT)) for p in target.rglob("*") if p.is_file()
        )
        logger.info("list_files: %s → %d files", target, len(files))
        return {"files": files}
