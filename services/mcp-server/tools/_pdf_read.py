"""PDF extraction helpers for sandboxed file reads.

Extracted from ``_file_helpers.py`` to keep that module under SLOC budget.
``_FORMAT_READERS`` in ``_file_helpers.py`` wires ``.pdf`` to ``_read_pdf_text``
imported from here.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from pathlib import Path

from mcp_events import monotonic_now, record

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


def extract_pdf_plaintext_with_timeout(
    path: Path,
    timeout_s: float = PDF_READ_TIMEOUT_S,
) -> str:
    """Run ``_extract_pdf_plaintext`` behind a wall-clock cap for search paths.

    Converts a pymupdf hang into ``TimeoutError`` so directory scans can count
    the file in ``skipped_converted`` instead of stalling past the remote MCP
    window (decision:mcp-fs-timeout-observability).
    """
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mcp-pdf-search")
    future = executor.submit(_extract_pdf_plaintext, path)
    try:
        return future.result(timeout=timeout_s)
    except FutureTimeoutError as exc:
        record(
            "mcp.fs.search.pdf.plaintext.timeout",
            path=str(path),
            timeout_s=timeout_s,
        )
        raise TimeoutError(
            f"PDF plaintext extraction exceeded {timeout_s:.0f}s for {path.name}"
        ) from exc
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def _extract_pdf_markdown(path: Path) -> tuple[str, str]:
    """Run PDF extraction without timeout handling.

    Returns ``(text, method)`` where ``method`` is one of:
      - ``PDF_METHOD_LAYOUT`` — pymupdf4llm produced usable markdown.
      - ``PDF_METHOD_PLAINTEXT_EMPTY`` — pymupdf4llm returned only scaffolding
        (e.g. ``"## \\n\\n## \\n\\n"``); fell back to plaintext extraction.
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
