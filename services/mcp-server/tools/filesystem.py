"""Filesystem tools — sandboxed read/write/list in /data/files.

All paths are resolved relative to _SANDBOX_ROOT. Traversal attempts
(../) are rejected before resolution so that the container volume mount
is complemented by explicit code-level defense in depth.

Supported write formats: .md, .txt (plain), .docx (python-docx), .pdf (fpdf2).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = logging.getLogger(__name__)

_SANDBOX_ROOT = Path("/data/files")
_ALLOWED_WRITE_SUFFIXES = {".md", ".txt", ".docx", ".pdf"}


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

        Only plain-text files (.md, .txt) are supported for reading.
        Binary formats (.docx, .pdf) are not decoded.

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

        content = src.read_text(encoding="utf-8", errors="replace")
        logger.info("read_file: read %s (%d chars)", src, len(content))
        return {"content": content, "path": str(src)}

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
