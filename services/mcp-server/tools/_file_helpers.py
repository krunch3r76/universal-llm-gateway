"""Shared helpers for sandboxed file reads under /data/files."""

from __future__ import annotations

import base64
import mimetypes
from pathlib import Path
from typing import Any

from mcp_events import record

from ._hashing import sha256_hex_of_file
from ._line_range import apply_line_range
from ._operating_state_serve import apply_operating_state_serve
from ._pdf_read import (
    PDF_LAYOUT_MAX_BYTES,
    PDF_METHOD_LAYOUT,
    PDF_METHOD_PLAINTEXT_EMPTY,
    PDF_METHOD_PLAINTEXT_GATED,
    PDF_METHOD_PLAINTEXT_TIMEOUT,
    PDF_METHOD_SIDECAR,
    PDF_READ_TIMEOUT_S,
    _read_pdf_text,
    extract_pdf_plaintext_with_timeout,
    read_pdf,
)

# Method tags for search text loading (load_searchable_text). PDF routes reuse
# PDF_METHOD_SIDECAR; the remaining tags are search-specific.
SEARCH_METHOD_PDF_PLAINTEXT = "pymupdf_plaintext"
SEARCH_METHOD_CONVERTED = "converted"

FILES_ROOT = Path("/data/files")

# Extensions whose content is binary and must not be decoded as UTF-8 text.
# When binary=False is requested for one of these, read_file_result auto-routes
# to binary mode and sets auto_binary=True in the result so callers can detect it.
BINARY_EXTENSIONS: frozenset[str] = frozenset(
    {
        # Images
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".bmp",
        ".tiff",
        ".tif",
        ".heic",
        ".heif",
        # Video / audio
        ".mp3",
        ".mp4",
        ".mov",
        ".avi",
        ".mkv",
        # Archives
        ".zip",
        ".tar",
        ".gz",
        ".tgz",
        ".bz2",
        ".xz",
        ".7z",
        ".rar",
        ".whl",
    }
)


def is_binary_by_magic(path: Path) -> bool:
    """Return True when magic bytes identify *path* as a binary format.

    Uses the `filetype` library (reads first 261 bytes) as a content-based
    fallback for files whose extension is absent or mismatched. Returns False
    when filetype is unavailable or the file is unrecognised (safe fallback).
    """
    try:
        import filetype  # type: ignore[import-untyped]
    except ImportError:
        return False  # pre-rebuild fallback: filetype not yet installed
    try:
        return filetype.guess(path) is not None
    except (OSError, ValueError) as exc:
        record("mcp.fs.binary.detect.failed", path=str(path), error=str(exc))
        return False


def _read_plain(path: Path) -> str:
    """Reads the content of a plain text file, replacing decoding errors."""
    return path.read_text(encoding="utf-8", errors="replace")


def read_docx(path: Path) -> str:
    from docx import Document

    doc = Document(str(path))
    return "\n".join(para.text for para in doc.paragraphs)


def read_odt(path: Path) -> str:
    from odf import teletype
    from odf.opendocument import load as odf_load
    from odf.text import P

    doc = odf_load(str(path))
    return "\n".join(teletype.extractText(node) for node in doc.getElementsByType(P))


def _read_html(path: Path) -> str:
    """Convert an HTML file to markdown prose using html2text.

    Preserves structural text while stripping tags. Does not synthesize
    headings from class-based markup (e.g. <strong class='heading-2'>) —
    the full body is emitted as a single prose preamble which md_read can
    access via section="" (preamble). body_width=0 disables line-wrapping.
    """
    import html2text

    h = html2text.HTML2Text()
    h.body_width = 0
    h.ignore_images = True
    raw = path.read_text(encoding="utf-8", errors="replace")
    try:
        return h.handle(raw)
    except Exception as exc:
        record("mcp.fs.html.parse.failed", path=str(path), error=str(exc))
        raise


def read_eml(path: Path) -> str:
    """Reads the content of an EML file, extracting headers, body, and attachment content."""
    from document_text.eml import parse_eml_file

    return parse_eml_file(path).text


def _pdf_sidecar_candidates(path: Path) -> list[Path]:
    """Return markdown sidecars that should satisfy a PDF read without extraction."""
    return [
        path.with_name(f"{path.stem}-readable.md"),
        path.with_name(f"{path.stem}.readable.md"),
        path.with_suffix(".extracted.md"),
    ]


_FORMAT_READERS: dict[str, object] = {
    ".docx": read_docx,
    ".html": _read_html,
    ".htm": _read_html,
    ".odt": read_odt,
    ".eml": read_eml,
    ".pdf": _read_pdf_text,
}


def extract_text_content(path: Path) -> str:
    """Extract text from *path* using format-specific readers when available.

    Falls back to plain UTF-8 read for unrecognized suffixes.  PDF is converted
    to markdown via ``pymupdf4llm``; DOCX/ODT produce plain paragraphs; EML
    extracts headers + body + PDF attachments; HTML is converted to markdown
    prose via ``html2text``.
    """
    reader = _FORMAT_READERS.get(path.suffix.lower(), _read_plain)
    return reader(path)  # type: ignore[operator]


def is_converted_format(path: Path) -> bool:
    """True when *path* requires format conversion (not natively UTF-8 text)."""
    return path.suffix.lower() in _FORMAT_READERS


def load_searchable_text(path: Path) -> tuple[str, str | None]:
    """Load text for regex search, sidecar-stat-first.

    Satisfies ``decision:mcp-fs-timeout-observability`` (agent-bus:962):
    durable readable sidecars are preferred before any PDF extraction. PDFs
    without a sidecar use layout-free plaintext extraction
    (``_extract_pdf_plaintext``, <1s) rather than the pymupdf4llm layout pass,
    so directory scans stay within the remote-connector wall-clock window.
    Other converted formats route through ``extract_text_content``; native
    text is read directly.

    Returns ``(text, method)`` where method is one of:
      - ``sidecar_markdown`` — read from a markdown sidecar
      - ``pymupdf_plaintext`` — PDF extracted layout-free (timeout-bounded)
      - ``converted`` — non-PDF converted format (DOCX/ODT/EML/HTML)
      - ``None`` — native UTF-8 text (search envelope reports ``native_text``)
    """
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        sidecar = next((c for c in _pdf_sidecar_candidates(path) if c.is_file()), None)
        if sidecar is not None:
            return sidecar.read_text(
                encoding="utf-8", errors="replace"
            ), PDF_METHOD_SIDECAR
        return (
            extract_pdf_plaintext_with_timeout(path),
            SEARCH_METHOD_PDF_PLAINTEXT,
        )
    if is_converted_format(path):
        return extract_text_content(path), SEARCH_METHOD_CONVERTED
    return path.read_text(encoding="utf-8", errors="replace"), None


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
    offset: int = 0,
    limit: int = 0,
) -> dict[str, Any]:
    """Read one sandboxed file and return the standard MCP payload shape.

    Successful responses include ``read_sha256``: bare lowercase hex SHA-256 of
    the on-disk source file bytes, computed before decode/conversion/windowing.
    """
    if offset < 0:
        raise ValueError("offset must be >= 0")
    if limit < 0:
        raise ValueError("limit must be >= 0")
    range_requested = offset > 0 or limit > 0

    src = resolve_files_path(path, root=root)
    if not src.exists():
        raise FileNotFoundError(f"File not found: {path!r}")
    if not src.is_file():
        raise ValueError(f"Path is not a file: {path!r}")

    read_sha256 = sha256_hex_of_file(src)

    served = apply_operating_state_serve(
        path,
        src,
        read_sha256,
        binary=binary,
        offset=offset,
        limit=limit,
    )
    if served is not None:
        return served

    if binary:
        result = build_binary_read_result(src)
        result["read_sha256"] = read_sha256
        if range_requested:
            result["line_range_applied"] = False
        return result

    suffix = src.suffix.lower()

    # Auto-route binary files — returning binary-as-text corrupts MCP wire payloads.
    # Fast path: known extension (zero I/O). Fallback: magic-byte probe (261 bytes).
    # ∀ converted formats (PDF, DOCX, …): skip magic probe — dedicated readers exist
    # and filetype would wrongly route them to base64, losing text extraction.
    if suffix in BINARY_EXTENSIONS:
        auto_result = build_binary_read_result(src)
        auto_result["auto_binary"] = True
    elif suffix not in _FORMAT_READERS and is_binary_by_magic(src):
        record("mcp.fs.binary.magic.match", path=str(src))
        auto_result = build_binary_read_result(src)
        auto_result["auto_binary"] = True
    else:
        auto_result = None

    if auto_result is not None:
        auto_result["read_sha256"] = read_sha256
        if range_requested:
            auto_result["line_range_applied"] = False
        if auto_result.get("mime_type", "").startswith("image/"):
            auto_result["_next"] = (
                f'For text extraction: dispatch(tool="extract_document", '
                f'arguments=\'{{"path": "{path}"}}\').'
                f' For visual inspection: view_image(path="{path}").'
            )
        return auto_result
    sidecar_path: Path | None = None
    if suffix == ".pdf":
        sidecar_path = next(
            (
                candidate
                for candidate in _pdf_sidecar_candidates(src)
                if candidate.is_file()
            ),
            None,
        )

    if suffix == ".pdf" and sidecar_path is None:
        # Direct call for method-tag visibility. The PDF reader returns
        # (text, method) so consumers can distinguish layout, plaintext-gated,
        # plaintext-timeout-fallback, and plaintext-empty-result routes.
        content, pdf_method = read_pdf(src)
    elif sidecar_path is not None:
        content = sidecar_path.read_text(encoding="utf-8", errors="replace")
        pdf_method = PDF_METHOD_SIDECAR
    else:
        content = extract_text_content(src)
        pdf_method = ""  # non-PDF — unused below

    result: dict[str, Any] = {"content": content, "path": str(src)}
    if suffix == ".pdf":
        content_stripped = content.strip()
        is_empty = len(content_stripped) < 50
        result["extraction_method"] = pdf_method
        if sidecar_path:
            result["sidecar_path"] = str(sidecar_path)
        if pdf_method == PDF_METHOD_SIDECAR:
            advisory_prefix = "Read from pre-extracted markdown sidecar. "
        elif pdf_method == PDF_METHOD_LAYOUT:
            advisory_prefix = "Extracted with pymupdf4llm (prose-oriented). "
        elif pdf_method == PDF_METHOD_PLAINTEXT_GATED:
            advisory_prefix = (
                "Extracted with pymupdf plaintext — file exceeded "
                f"{PDF_LAYOUT_MAX_BYTES} bytes, layout pass skipped. "
            )
        elif pdf_method == PDF_METHOD_PLAINTEXT_TIMEOUT:
            advisory_prefix = (
                "Extracted with pymupdf plaintext — pymupdf4llm layout pass "
                f"exceeded {PDF_READ_TIMEOUT_S:.0f}s, fallback used. "
            )
        elif pdf_method == PDF_METHOD_PLAINTEXT_EMPTY:
            advisory_prefix = (
                "Extracted with pymupdf plaintext — pymupdf4llm returned "
                "empty/scaffolding output, fallback used. "
            )
        else:
            advisory_prefix = "Extracted from PDF. "
        result["extraction"] = {
            "method": pdf_method,
            "kind": "prose_oriented",
            "advisory": advisory_prefix
            + (
                "If output has garbled tables or columns, try "
                'finance(op="inspect", path=...) for pdfplumber-based '
                "tabular extraction."
            ),
        }
        if is_empty:
            rel_path = str(Path(path))
            result["_next"] = (
                f"This PDF has no text layer (scanned or image-only). "
                f'Use dispatch(tool="extract_document", '
                f'arguments=\'{{"path": "{rel_path}"}}\') '
                f"to OCR and persist as a reusable markdown sidecar."
            )
    if range_requested:
        content, range_meta = apply_line_range(content, offset, limit)
        result["content"] = content
        result.update(range_meta)
        returned = range_meta.get("line_range", {})
        if isinstance(returned, dict) and returned.get("returned") == 0:
            result["observation"] = (
                "Read succeeded; the requested line window returned zero lines "
                f"(offset={offset}, limit={limit}, total_lines={range_meta.get('total_lines')}). "
                "Empty content ≠ missing file."
            )
    elif content == "":
        result["observation"] = (
            "Read succeeded; file decoded to empty text (zero-byte or whitespace-only content)."
        )
    result["read_sha256"] = read_sha256
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
            if binary or result.get("auto_binary"):
                results[path] = result
            else:
                results[path] = result["content"]
        except Exception as exc:
            results[path] = {"error": str(exc)}
    return results
