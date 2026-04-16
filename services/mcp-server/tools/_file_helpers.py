"""Shared helpers for sandboxed file reads under /data/files."""

from __future__ import annotations

import base64
import mimetypes
from pathlib import Path
from typing import Any

FILES_ROOT = Path("/data/files")
ALLOWED_READ_SUFFIXES = {
    ".md",
    ".txt",
    ".csv",
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


def _read_plain(path: Path) -> str:
    """Reads the content of a plain text file, replacing decoding errors."""
    return path.read_text(encoding="utf-8", errors="replace")


def _read_docx(path: Path) -> str:
    from docx import Document

    doc = Document(str(path))
    return "\n".join(para.text for para in doc.paragraphs)


def _read_odt(path: Path) -> str:
    from odf import teletype
    from odf.opendocument import load as odf_load
    from odf.text import P

    doc = odf_load(str(path))
    return "\n".join(teletype.extractText(node) for node in doc.getElementsByType(P))


def _read_pdf(path: Path) -> str:
    """Reads the content of a PDF file and returns it as markdown."""
    import pymupdf4llm  # type: ignore[import-untyped]

    return pymupdf4llm.to_markdown(str(path))


def _read_eml(path: Path) -> str:
    """Reads the content of an EML file, extracting headers, body, and attachment content.

    Prioritizes plain text body over HTML. Extracts PDF attachments as markdown.
    """
    import email
    import email.policy

    with path.open("rb") as file_handle:
        msg = email.message_from_binary_file(file_handle, policy=email.policy.default)

    lines: list[str] = []
    lines.append("---")
    for header in ("from", "to", "cc", "subject", "date", "message-id"):
        value = msg.get(header, "")
        if value:
            lines.append(f"{header}: {value}")
    lines.append("---")
    lines.append("")

    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            disposition = str(part.get("Content-Disposition", ""))
            if content_type == "text/plain" and "attachment" not in disposition:
                body = part.get_content()
                break
        if not body:
            for part in msg.walk():
                content_type = part.get_content_type()
                disposition = str(part.get("Content-Disposition", ""))
                if content_type == "text/html" and "attachment" not in disposition:
                    import html2text

                    body = html2text.html2text(part.get_content())
                    break
    else:
        body = msg.get_content()

    lines.append(body.strip())

    for part in msg.walk():
        filename = part.get_filename()
        if not filename:
            continue
        lines.append("")
        lines.append("---")
        lines.append(f"## Attachment: {filename}")
        lines.append("")
        content_type = part.get_content_type()
        if content_type == "application/pdf":
            import pymupdf  # type: ignore[import-untyped]
            import pymupdf4llm  # type: ignore[import-untyped]

            data = part.get_payload(decode=True)
            doc = None
            try:
                doc = pymupdf.open(stream=data, filetype="pdf")
                text = pymupdf4llm.to_markdown(doc)
            finally:
                if doc:
                    doc.close()
            lines.append(text.strip())
        else:
            lines.append(f"[{content_type} — not extracted]")

    return "\n".join(lines)


_FORMAT_READERS: dict[str, object] = {
    ".docx": _read_docx,
    ".odt": _read_odt,
    ".eml": _read_eml,
    ".pdf": _read_pdf,
}


def extract_text_content(path: Path) -> str:
    """Extract text from *path* using format-specific readers when available.

    Falls back to plain UTF-8 read for unrecognized suffixes.  PDF is converted
    to markdown via ``pymupdf4llm``; DOCX/ODT produce plain paragraphs; EML
    extracts headers + body + PDF attachments.
    """
    reader = _FORMAT_READERS.get(path.suffix.lower(), _read_plain)
    return reader(path)  # type: ignore[operator]


def is_converted_format(path: Path) -> bool:
    """True when *path* requires format conversion (not natively UTF-8 text)."""
    return path.suffix.lower() in _FORMAT_READERS


def resolve_files_path(relative: str, root: Path = FILES_ROOT) -> Path:
    """Resolve *relative* inside *root*, rejecting traversal attempts."""
    clean = relative.lstrip("/")
    candidate = (root / clean).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(
            f"Path {relative!r} resolves outside sandbox; traversal rejected"
        ) from exc
    return candidate


def build_binary_read_result(
    src: Path,
    *,
    path_value: str | None = None,
) -> dict[str, Any]:
    """Return a standard base64 payload for binary-safe file reads."""
    raw = src.read_bytes()
    mime_type, _ = mimetypes.guess_type(src.name)
    return {
        "path": path_value or str(src),
        "content_base64": base64.b64encode(raw).decode("ascii"),
        "mime_type": mime_type or "application/octet-stream",
        "encoding": "base64",
        "bytes": len(raw),
        "is_binary": True,
    }


def read_file_result(
    path: str,
    root: Path = FILES_ROOT,
    *,
    binary: bool = False,
) -> dict[str, Any]:
    """Read one sandboxed file and return the standard MCP payload shape."""
    src = resolve_files_path(path, root=root)
    if not src.exists():
        raise FileNotFoundError(f"File not found: {path!r}")
    if not src.is_file():
        raise ValueError(f"Path is not a file: {path!r}")

    if binary:
        return build_binary_read_result(src)

    suffix = src.suffix.lower()
    if suffix not in ALLOWED_READ_SUFFIXES:
        raise ValueError(
            f"Unsupported format {suffix!r} for reading. "
            f"Allowed: {', '.join(sorted(ALLOWED_READ_SUFFIXES))}"
        )

    content = extract_text_content(src)
    result: dict[str, Any] = {"content": content, "path": str(src)}
    if suffix == ".pdf":
        content_stripped = content.strip()
        is_empty = len(content_stripped) < 50
        result["extraction_method"] = "pymupdf4llm"
        result["extraction"] = {
            "method": "pymupdf4llm",
            "kind": "prose_oriented",
            "advisory": (
                "Extracted with pymupdf4llm (prose-oriented). "
                "If output has garbled tables or columns, try "
                "finance_extract_pdf(path=...) for pdfplumber-based "
                "tabular extraction."
            ),
        }
        if is_empty:
            rel_path = str(Path(path))
            result["_next"] = (
                f"This PDF has no text layer (scanned or image-only). "
                f"Use dispatch(tool=\"document_ocr\", "
                f"arguments='{{\"path\": \"{rel_path}\"}}') "
                f"for vision-based OCR, or "
                f"dispatch(tool=\"ingest_document\", "
                f"arguments='{{\"path\": \"{rel_path}\"}}') "
                f"to OCR and persist as a reusable markdown sidecar."
            )
    return result


def read_files_batch(
    paths: list[str],
    root: Path = FILES_ROOT,
    *,
    binary: bool = False,
) -> dict[str, Any]:
    """Read multiple sandboxed files, preserving per-path errors inline."""
    results: dict[str, Any] = {}
    for path in paths:
        try:
            result = read_file_result(path, root=root, binary=binary)
            results[path] = result if binary else result["content"]
        except Exception as exc:
            results[path] = {"error": str(exc)}
    return results
