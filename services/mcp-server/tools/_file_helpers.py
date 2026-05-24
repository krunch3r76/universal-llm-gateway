"""Shared helpers for sandboxed file reads under /data/files."""

from __future__ import annotations

import base64
import mimetypes
import os
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from pathlib import Path
from typing import Any

from mcp_events import monotonic_now, record

FILES_ROOT = Path("/data/files")
PDF_READ_TIMEOUT_S = 25.0  # 5s headroom before the observed ~30s remote-disconnect window (cortex 9119; agent-bus:962)
PDF_LAYOUT_MAX_BYTES = int(os.getenv("MCP_PDF_LAYOUT_MAX_BYTES", str(2 * 1024 * 1024)))
# 2 MB heuristic — observed timeouts (cortex friction 9742) spanned 1–3 MB
# PDFs; threshold sits at mid-range. Tune via env if sub-2-MB timeouts surface.

# Method tags for PDF extraction route reporting. Used by read_file_result to
# expose the actual route to consumers per [universal:rest] projection-fidelity.
PDF_METHOD_LAYOUT = "pymupdf4llm"
PDF_METHOD_SIDECAR = "sidecar_markdown"
PDF_METHOD_PLAINTEXT_GATED = "pymupdf_plaintext_size_gated"
PDF_METHOD_PLAINTEXT_TIMEOUT = "pymupdf_plaintext_timeout_fallback"
PDF_METHOD_PLAINTEXT_EMPTY = "pymupdf_plaintext_empty_result"

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


def _extract_pdf_plaintext(path: Path) -> str:
    """Fast plain-text extraction via pymupdf without layout analysis.

    Completes in well under 1s on any PDF that pymupdf can open. Used as the
    timeout fallback and size-gate path when pymupdf4llm layout analysis is
    not viable within the provider deadline.
    """
    import pymupdf  # type: ignore[import-untyped]

    doc = pymupdf.open(str(path))
    try:
        return "\n\n".join(page.get_text("text") for page in doc)
    finally:
        doc.close()


def _extract_pdf_markdown(path: Path) -> tuple[str, str]:
    """Run PDF extraction without timeout handling.

    Returns ``(text, method)`` where ``method`` is one of:
      - ``PDF_METHOD_LAYOUT`` — pymupdf4llm produced usable markdown.
      - ``PDF_METHOD_PLAINTEXT_EMPTY`` — pymupdf4llm returned only scaffolding
        (e.g. ``"## \n\n## \n\n"``); fell back to plaintext extraction.
    """
    import pymupdf4llm  # type: ignore[import-untyped]

    text = pymupdf4llm.to_markdown(str(path), use_ocr=False)

    # pymupdf4llm >= 1.27 with layout engine can produce only markdown-syntax
    # scaffolding (e.g. "## \n\n## \n\n") with no alphanumeric content — not
    # detected as empty by .strip() alone.  Count alnum chars as the signal.
    if sum(1 for c in text if c.isalnum()) >= 50:
        return text, PDF_METHOD_LAYOUT

    record(
        "mcp.tool.pdf.read.plaintext",
        cause="empty_result_fallback",
        path=str(path),
    )
    # Fallback: direct fitz extraction for PDFs where pymupdf4llm returns empty.
    return _extract_pdf_plaintext(path), PDF_METHOD_PLAINTEXT_EMPTY


def read_pdf(path: Path, timeout_s: float = PDF_READ_TIMEOUT_S) -> tuple[str, str]:
    """Read a PDF and return ``(text, method)`` where ``method`` names the route taken.

    pymupdf4llm ≥ 1.27 enables OCR by default via ``use_ocr=True``, causing
    indefinite hangs on scanned/image-only PDFs.  Pass ``use_ocr=False`` to keep
    extraction fast; absorbed as ``**kwargs`` in older versions (≤ 0.3.x) so the
    call is safe across versions.  Fall back to direct fitz plain-text
    extraction when pymupdf4llm returns an empty result.

    The extractor runs behind a wall-clock timeout so remote MCP clients
    receive a visible failure before the observed ~30s remote-disconnect
    window cuts the stream.

    Heavy PDFs (> ``PDF_LAYOUT_MAX_BYTES``) bypass pymupdf4llm entirely —
    layout analysis on 1–3 MB rich-layout PDFs routinely exceeds the timeout
    budget.  On a layout-pass timeout the plain-text fallback is attempted
    before raising, so clients always receive content when pymupdf can open
    the file.

    Timeout semantics — ``future.result(timeout=...)`` returns control to
    the caller in ``timeout_s``, but Python cannot interrupt a running native
    call inside ``pymupdf4llm.to_markdown(...)``.  The layout-extraction
    thread continues in the background until pymupdf4llm completes; its
    result is then discarded.  During this window the plaintext fallback
    (< 1s on any pymupdf-openable PDF) shares CPU/memory with the still-
    running layout pass.  Bounded by the per-call executor — no long-term
    leak — but the docstring's promise is "timely return to caller", not
    "extraction cancelled".

    Engine boundary — the plaintext fallback uses pymupdf's lower-level
    ``page.get_text("text")``, the same C engine as pymupdf4llm with the
    layout pass disabled.  If pymupdf itself hangs (encrypted streams,
    malformed xref), the fallback inherits the hang.  Cortex friction 9742
    suggested an independent engine (pdfplumber/pdftotext); deferred to a
    separate change — escalate if a hang on a pymupdf-openable PDF appears
    in ``mcp.tool.pdf.read.timeout`` with no following ``.plaintext`` event.

    Method tags returned:
      - ``PDF_METHOD_PLAINTEXT_GATED`` — size-gate; pymupdf4llm skipped.
      - ``PDF_METHOD_LAYOUT`` — pymupdf4llm produced usable markdown.
      - ``PDF_METHOD_PLAINTEXT_EMPTY`` — pymupdf4llm returned scaffolding;
        plaintext fallback succeeded inside ``_extract_pdf_markdown``.
      - ``PDF_METHOD_PLAINTEXT_TIMEOUT`` — pymupdf4llm exceeded ``timeout_s``;
        plaintext fallback succeeded.
    """
    # ∀ file_size > PDF_LAYOUT_MAX_BYTES: layout analysis is not viable within
    # the provider deadline; go straight to plain-text.
    file_size = path.stat().st_size
    if file_size > PDF_LAYOUT_MAX_BYTES:
        record(
            "mcp.tool.pdf.read.plaintext",
            cause="gated",
            path=str(path),
            size_bytes=file_size,
            threshold_bytes=PDF_LAYOUT_MAX_BYTES,
        )
        return _extract_pdf_plaintext(path), PDF_METHOD_PLAINTEXT_GATED

    t0 = monotonic_now()
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mcp-pdf-read")
    future = executor.submit(_extract_pdf_markdown, path)
    try:
        return future.result(timeout=timeout_s)
    except FutureTimeoutError:
        elapsed = monotonic_now() - t0
        # Advisory only — Python cannot interrupt a running pymupdf4llm thread.
        # Documented in the docstring; the thread completes in the background.
        future.cancel()
        record(
            "mcp.tool.pdf.read.timeout",
            path=str(path),
            extension=path.suffix.lower(),
            elapsed_s=round(elapsed, 3),
            timeout_s=timeout_s,
            fallback="plaintext",
        )
        # Retry with layout-free extraction — completes in <1s on any openable PDF.
        try:
            text = _extract_pdf_plaintext(path)
            record(
                "mcp.tool.pdf.read.plaintext",
                cause="timeout_fallback",
                path=str(path),
                elapsed_layout_s=round(elapsed, 3),
            )
            return text, PDF_METHOD_PLAINTEXT_TIMEOUT
        except Exception as fallback_exc:
            raise TimeoutError(
                f"PDF extraction exceeded {timeout_s:.0f}s for {path.name}; "
                f"plain-text fallback also failed: {fallback_exc}"
            ) from fallback_exc
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def _read_pdf_text(path: Path) -> str:
    """Adapter for ``_FORMAT_READERS`` — returns text only, discards method tag."""
    text, _method = read_pdf(path)
    return text


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
                "finance_extract_pdf(path=...) for pdfplumber-based "
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
