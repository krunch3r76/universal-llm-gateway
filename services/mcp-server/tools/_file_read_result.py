"""Single-path sandboxed file read.

Extracted from ``_file_helpers.py`` so that module stays within the SLOC ceiling.
``read_file_result`` is re-exported from ``_file_helpers``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mcp_events import record

from ._file_helpers import (
    _FORMAT_READERS,
    BINARY_EXTENSIONS,
    FILES_ROOT,
    _pdf_bytes_unreadable,
    _pdf_sidecar_candidates,
    build_binary_read_result,
    extract_text_content,
    is_binary_by_magic,
    resolve_files_path,
)
from ._hashing import sha256_hex_equal, sha256_hex_of_file
from ._line_range import apply_line_range, exceeds_unwindowed_read_cap
from ._operating_state_serve import apply_operating_state_serve
from ._pdf_read import (
    PDF_LAYOUT_MAX_BYTES,
    PDF_METHOD_LAYOUT,
    PDF_METHOD_PLAINTEXT_EMPTY,
    PDF_METHOD_PLAINTEXT_GATED,
    PDF_METHOD_PLAINTEXT_TIMEOUT,
    PDF_METHOD_SIDECAR,
    PDF_READ_TIMEOUT_S,
    _attach_pdf_read_metadata,
    read_pdf,
)
from ._sparse_document_read import (
    _stale_read_root,
    is_sparse_document_suffix,
    param_not_applicable_error,
    resolve_document_read,
)
from ._unwindowed_read_projection import build_unwindowed_oversized_payload


def read_file_result(
    path: str,
    root: Path = FILES_ROOT,
    *,
    binary: bool = False,
    offset: int = 0,
    limit: int = 0,
    section: str | None = None,
    sections: list[str] | None = None,
    export: bool = False,
    chunk_cursor: int | None = None,
    chunk_cursor_provided: bool = False,
    expected_read_sha256: str | None = None,
    sandbox: str = "cortex",
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
    suffix_early = src.suffix.lower()
    document = is_sparse_document_suffix(suffix_early)
    inapplicable: list[str] = []
    if chunk_cursor_provided:
        inapplicable.append("chunk_cursor")
    if expected_read_sha256:
        inapplicable.append("expected_read_sha256")
    if export:
        inapplicable.append("export")
    if sections:
        inapplicable.append("sections")
    if section and str(section).strip():
        inapplicable.append("section")
    if not document and inapplicable:
        return param_not_applicable_error(
            parameter=inapplicable[0],
            keys=inapplicable,
        )
    if document and expected_read_sha256 and range_requested:
        return param_not_applicable_error(
            parameter="expected_read_sha256",
            keys=["expected_read_sha256"],
        )
    if (
        document
        and expected_read_sha256
        and not sha256_hex_equal(read_sha256, expected_read_sha256)
    ):
        stale = _stale_read_root(
            read_sha256=read_sha256,
            expected_read_sha256=expected_read_sha256,
        )
        stale["path"] = path
        return stale

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

    sparse_doc = (
        not binary and auto_result is None and is_sparse_document_suffix(suffix)
    )
    if sparse_doc and suffix == ".pdf" and not export and not range_requested:
        from ._pdf_sections import resolve_pdf_outline_read

        outline_result: dict[str, Any] | None
        try:
            outline_result = resolve_pdf_outline_read(
                file_path=src,
                path=path,
                sandbox=sandbox,
                read_sha256=read_sha256,
                section=section,
                sections=sections,
                chunk_cursor=chunk_cursor,
                chunk_cursor_provided=chunk_cursor_provided,
                expected_read_sha256=expected_read_sha256 or None,
            )
        except Exception as exc:
            # Unreadable bytes keep the sidecar markdown path. A PDF that
            # opens stays in the TOC id space even when a sidecar exists.
            if sidecar_path is None or not _pdf_bytes_unreadable(exc):
                raise
            outline_result = None
        if outline_result is not None:
            if "code" in outline_result or "error" in outline_result:
                outline_result["path"] = path
                return outline_result
            _attach_pdf_read_metadata(
                outline_result,
                path=path,
                content=content,
                pdf_method=pdf_method,
                sidecar_path=sidecar_path,
            )
            outline_result["path"] = path
            return outline_result

    if sparse_doc:
        sparse_result = resolve_document_read(
            path=path,
            sandbox=sandbox,
            text=content,
            read_sha256=read_sha256,
            section=section,
            sections=sections,
            export=export,
            offset=offset,
            limit=limit,
            chunk_cursor=chunk_cursor,
            chunk_cursor_provided=chunk_cursor_provided,
            expected_read_sha256=expected_read_sha256 or None,
        )
        if "code" in sparse_result or "error" in sparse_result:
            sparse_result["path"] = path
            return sparse_result
        if suffix == ".pdf":
            _attach_pdf_read_metadata(
                sparse_result,
                path=path,
                content=content,
                pdf_method=pdf_method,
                sidecar_path=sidecar_path,
            )
        sparse_result["path"] = path
        return sparse_result

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
    elif exceeds_unwindowed_read_cap(content):
        if suffix in {".md", ".markdown"}:
            projected = resolve_document_read(
                path=path,
                sandbox=sandbox,
                text=content,
                read_sha256=read_sha256,
            )
            projected["path"] = path
            return projected
        projected = build_unwindowed_oversized_payload(
            request_path=path,
            text=content,
        )
        result.update(projected)
        result["path"] = path
    elif content == "":
        result["observation"] = (
            "Read succeeded; file decoded to empty text (zero-byte or whitespace-only content)."
        )
    result["read_sha256"] = read_sha256
    return result
