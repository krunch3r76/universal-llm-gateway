"""Shared helpers for sandboxed file reads under /data/files."""

from __future__ import annotations

from pathlib import Path

FILES_ROOT = Path("/data/files")
ALLOWED_READ_SUFFIXES = {
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


def read_file_result(path: str, root: Path = FILES_ROOT) -> dict[str, str]:
    """Read one sandboxed file and return the standard MCP payload shape."""
    src = resolve_files_path(path, root=root)
    if not src.exists():
        raise FileNotFoundError(f"File not found: {path!r}")
    if not src.is_file():
        raise ValueError(f"Path is not a file: {path!r}")

    suffix = src.suffix.lower()
    if suffix not in ALLOWED_READ_SUFFIXES:
        raise ValueError(
            f"Unsupported format {suffix!r} for reading. "
            f"Allowed: {', '.join(sorted(ALLOWED_READ_SUFFIXES))}"
        )

    read_handlers = {
        ".docx": _read_docx,
        ".odt": _read_odt,
        ".eml": _read_eml,
        ".pdf": _read_pdf,
    }
    read_handler = read_handlers.get(suffix, _read_plain)
    content = read_handler(src)
    return {"content": content, "path": str(src)}


def read_files_batch(
    paths: list[str],
    root: Path = FILES_ROOT,
) -> dict[str, str | dict[str, str]]:
    """Read multiple sandboxed files, preserving per-path errors inline."""
    results: dict[str, str | dict[str, str]] = {}
    for path in paths:
        try:
            results[path] = read_file_result(path, root=root)["content"]
        except Exception as exc:
            results[path] = {"error": str(exc)}
    return results
