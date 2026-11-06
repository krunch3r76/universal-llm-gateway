"""Shared helpers for sandboxed file reads under /data/files."""

from __future__ import annotations

import base64
import json
import mimetypes
from pathlib import Path
from typing import Any

from mcp_events import record

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
    _read_pdf_text,
    extract_pdf_plaintext_with_timeout,
    read_pdf,
)
from ._sparse_document_read import (
    BATCH_MAX_CONTENT_CHARS,
    _stale_read_root,
    finalize_sparse_document_payload,
    is_sparse_document_suffix,
    param_not_applicable_error,
    resolve_document_read,
    sparse_selector_conflict_error,
)
from ._unwindowed_read_projection import build_unwindowed_oversized_payload

# Method tags for search text loading (load_searchable_text). PDF routes reuse
# PDF_METHOD_SIDECAR; the remaining tags are search-specific.
SEARCH_METHOD_PDF_PLAINTEXT = "pymupdf_plaintext"
SEARCH_METHOD_CONVERTED = "converted"

FILES_ROOT = Path("/data/files")

_SPARSE_MANIFEST_CODE = "sparse_manifest_requires_expansion"


def sparse_manifest_batch_entry(result: dict[str, Any]) -> dict[str, Any]:
    """Structured fail-loud entry when a batch read got manifest, not body."""
    return {
        "code": _SPARSE_MANIFEST_CODE,
        "message": (
            "Document read returned selection=manifest without content; "
            "pass export=true or sections=[...] on read/md_read."
        ),
        "selection": result.get("selection", "manifest"),
        "read_sha256": result.get("read_sha256"),
        "path": result.get("path"),
    }


def _read_files_batch_value(result: dict[str, Any]) -> Any:
    """Keep structured sparse reads intact.

    Collapsing export/section/manifest payloads to a bare ``content`` string
    drops ``read_sha256``, ``truncated``, and ``chunk_cursor_next``, and a
    no-selector manifest must keep its descriptors.
    """
    if "error" in result or result.get("code"):
        return result
    if result.get("auto_binary") or "content_base64" in result:
        return result
    selection = result.get("selection")
    if selection in {"manifest", "section", "sections", "export", "window", "multi"}:
        return result
    if (
        result.get("items")
        or result.get("sparse_section_batch")
        or result.get("truncated")
        or result.get("chunk_cursor_next") is not None
    ):
        return result
    if "content" not in result and not binary_payload(result):
        return sparse_manifest_batch_entry(result)
    return result["content"]


def _pdf_bytes_unreadable(exc: BaseException) -> bool:
    """True when pymupdf could not open the file as a PDF."""
    name = type(exc).__name__
    return name in {"FileDataError", "FzErrorFormat", "EmptyFileError"}


def _path_match_keys(raw: str) -> set[str]:
    """Aliases that identify one path under the fs Share-URI grammar.

    ``workspaces://{repo}/{rel}`` matches both the sandbox-relative
    ``{repo}/{rel}`` spelling and the repo-stripped ``{rel}`` spelling.
    """
    text = str(raw).strip()
    keys: set[str] = set()
    if "://" in text:
        scheme, rest = text.split("://", 1)
        if scheme == "workspaces" and "/" in rest:
            repo, rel = rest.split("/", 1)
            repo = repo.strip("/")
            rel = rel.lstrip("/")
            if rel:
                keys.add(rel)
            if repo and rel:
                keys.add(f"{repo}/{rel}")
        else:
            bare = rest.lstrip("/")
            if bare:
                keys.add(bare)
    else:
        bare = text.lstrip("/")
        if bare:
            keys.add(bare)
    return keys


def _normalize_request_path(raw: str) -> str:
    """Sandbox-relative key for a path or its Share-URI spelling."""
    keys = _path_match_keys(raw)
    if not keys:
        return str(raw).strip().lstrip("/")
    return sorted(keys, key=len)[0]


def binary_payload(result: dict[str, Any]) -> bool:
    return bool(result.get("auto_binary") or result.get("content_base64"))

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
        not binary
        and auto_result is None
        and is_sparse_document_suffix(suffix)
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


def _attach_pdf_read_metadata(
    result: dict[str, Any],
    *,
    path: str,
    content: str,
    pdf_method: str,
    sidecar_path: Path | None,
) -> None:
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
    elif pdf_method:
        advisory_prefix = "Extracted from PDF. "
    else:
        advisory_prefix = ""
    if pdf_method:
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


def _is_sparse_path(rel: str, root: Path) -> bool:
    return is_sparse_document_suffix(resolve_files_path(rel, root=root).suffix.lower())


def _load_plain_text_for_sparse(rel: str, root: Path) -> str:
    """Load full document text the same way read_file_result does (PDF, etc.)."""
    src = resolve_files_path(rel, root=root)
    suffix = src.suffix.lower()
    if suffix == ".pdf":
        sidecar = next((c for c in _pdf_sidecar_candidates(src) if c.is_file()), None)
        if sidecar is None:
            text, _ = read_pdf(src)
        else:
            text = sidecar.read_text(encoding="utf-8", errors="replace")
    else:
        text = extract_text_content(src)
    return text


def read_files_batch(
    paths: list[str],
    root: Path = FILES_ROOT,
    *,
    binary: bool = False,
    section: str | None = None,
    sections: list[str] | None = None,
    export: bool = False,
    chunk_cursor: int | None = None,
    chunk_cursor_provided: bool = False,
    expected_read_sha256: str | dict[str, str] | None = None,
) -> dict[str, Any]:
    """Read multiple sandboxed files, preserving per-path errors inline."""
    if chunk_cursor_provided:
        return param_not_applicable_error(parameter="chunk_cursor")

    exp_map: dict[str, str] | None = None
    exp_scalar: str | None = None
    if isinstance(expected_read_sha256, dict):
        exp_map = dict(expected_read_sha256)
    elif expected_read_sha256:
        raw_exp = str(expected_read_sha256).strip()
        if raw_exp.startswith("{"):
            try:
                parsed = json.loads(raw_exp)
                if isinstance(parsed, dict):
                    exp_map = {str(k): str(v) for k, v in parsed.items()}
                else:
                    exp_scalar = raw_exp
            except json.JSONDecodeError:
                exp_scalar = raw_exp
        else:
            exp_scalar = raw_exp

    sparse_paths = [p for p in paths if _is_sparse_path(p, root)]
    if exp_scalar is not None and len(sparse_paths) > 1:
        return sparse_selector_conflict_error(
            "expected_read_sha256 scalar applies only when read_multi names one document path"
        )

    if exp_map is not None:
        requested: dict[str, str] = {}
        for path_item in paths:
            for alias in _path_match_keys(path_item):
                requested.setdefault(alias, path_item)
        bad_keys: list[str] = []
        resolved_map: dict[str, str] = {}
        for key, value in exp_map.items():
            target = None
            for alias in _path_match_keys(key):
                target = requested.get(alias)
                if target is not None:
                    break
            if target is None or not _is_sparse_path(target, root):
                bad_keys.append(str(key))
                continue
            resolved_map[target] = str(value)
        if bad_keys:
            return param_not_applicable_error(
                parameter="expected_read_sha256",
                keys=bad_keys,
            )
        exp_map = resolved_map

    def _per_path_expected(p: str) -> str | None:
        if exp_map is not None:
            return exp_map.get(p)
        if exp_scalar is not None and _is_sparse_path(p, root):
            return exp_scalar
        return None

    has_section = bool(section and str(section).strip())
    multi_path = len(paths) > 1
    multi_expanded = multi_path and (bool(sections) or export or has_section)

    if multi_expanded and sections:
        flat: list[dict[str, Any]] = []
        omitted: list[str] = []
        budget_cursor: int | None = None
        used = 0
        flat_idx = 0
        ended = False
        for rel in paths:
            for sid in list(sections or []):
                if ended:
                    flat.append(
                        {"path": rel, "id": sid, "outcome": "over_budget"}
                    )
                    omitted.append(f"{rel}:{sid}")
                    flat_idx += 1
                    continue
                per_exp = _per_path_expected(rel)
                one = read_file_result(
                    rel,
                    root=root,
                    binary=False,
                    sections=[sid],
                    expected_read_sha256=per_exp,
                )
                if one.get("outcome") == "stale_handle" or one.get("code") == "stale_handle":
                    flat.append(
                        {
                            "path": rel,
                            "id": sid,
                            "outcome": "stale_handle",
                            "read_sha256": one.get("read_sha256")
                            or (one.get("data") or {}).get("read_sha256"),
                        }
                    )
                    flat_idx += 1
                    continue
                it = (one.get("items") or [None])[0]
                if it is None:
                    flat.append(
                        {
                            "path": rel,
                            "id": sid,
                            "outcome": "error",
                            "error": "no batch item",
                        }
                    )
                    flat_idx += 1
                    continue
                oc = it.get("outcome")
                if oc == "stale_handle" or one.get("outcome") == "stale_handle":
                    flat.append(
                        {
                            "path": rel,
                            "id": sid,
                            "outcome": "stale_handle",
                            "read_sha256": one.get("read_sha256"),
                        }
                    )
                    flat_idx += 1
                    continue
                if oc != "ok":
                    flat.append({"path": rel, **it})
                    if oc == "over_budget":
                        ended = True
                        budget_cursor = flat_idx
                    flat_idx += 1
                    continue
                body = str(it.get("content", ""))
                next_used = used + len(body)
                if next_used > BATCH_MAX_CONTENT_CHARS:
                    ended = True
                    budget_cursor = flat_idx
                    flat.append(
                        {"path": rel, "id": sid, "outcome": "over_budget"}
                    )
                    omitted.append(f"{rel}:{sid}")
                    flat_idx += 1
                    continue
                used = next_used
                flat.append({"path": rel, **it})
                flat_idx += 1
        out: dict[str, Any] = {
            "selection": "multi",
            "read_multi": True,
            "items": flat,
            "sparse_section_batch": True,
        }
        if omitted:
            out["omitted"] = omitted
        if budget_cursor is not None:
            out["budget_cursor"] = budget_cursor
        return finalize_sparse_document_payload(out)

    if multi_expanded and export:
        items: list[dict[str, Any]] = []
        remaining = BATCH_MAX_CONTENT_CHARS
        terminated = False
        for rel in paths:
            if terminated:
                items.append(
                    {
                        "path": rel,
                        "outcome": "over_budget",
                        "fetch": {
                            "op": "read",
                            "path": rel,
                            "export": True,
                            "chunk_cursor": 0,
                        },
                    }
                )
                continue
            per_exp = _per_path_expected(rel)
            if not _is_sparse_path(rel, root):
                r = read_file_result(
                    rel,
                    root=root,
                    binary=False,
                    expected_read_sha256=per_exp,
                )
                body = str(r.get("content", ""))
                if len(body) > BATCH_MAX_CONTENT_CHARS:
                    items.append(
                        {
                            "path": rel,
                            "outcome": "over_budget",
                            "oversize": True,
                            "fetch": {
                                "op": "read",
                                "path": rel,
                                "export": True,
                                "chunk_cursor": 0,
                            },
                        }
                    )
                    terminated = True
                    continue
                if len(body) > remaining:
                    items.append(
                        {
                            "path": rel,
                            "outcome": "over_budget",
                            "fetch": {
                                "op": "read",
                                "path": rel,
                                "export": True,
                                "chunk_cursor": 0,
                            },
                        }
                    )
                    terminated = True
                    continue
                items.append({"path": rel, "content": body, "outcome": "ok"})
                remaining -= len(body)
                continue
            text = _load_plain_text_for_sparse(rel, root)
            body_len = len(text)
            if body_len > BATCH_MAX_CONTENT_CHARS:
                items.append(
                    {
                        "path": rel,
                        "outcome": "over_budget",
                        "oversize": True,
                        "fetch": {
                            "op": "read",
                            "path": rel,
                            "export": True,
                            "chunk_cursor": 0,
                        },
                    }
                )
                terminated = True
                continue
            if body_len > remaining:
                items.append(
                    {
                        "path": rel,
                        "outcome": "over_budget",
                        "fetch": {
                            "op": "read",
                            "path": rel,
                            "export": True,
                            "chunk_cursor": 0,
                        },
                    }
                )
                terminated = True
                continue
            src = resolve_files_path(rel, root=root)
            rs = sha256_hex_of_file(src)
            if per_exp and not sha256_hex_equal(rs, per_exp):
                items.append(
                    {
                        "path": rel,
                        "outcome": "stale_handle",
                        "read_sha256": rs,
                    }
                )
                continue
            chunk = finalize_sparse_document_payload(
                resolve_document_read(
                    path=rel,
                    sandbox="cortex",
                    text=text,
                    read_sha256=rs,
                    export=True,
                    expected_read_sha256=per_exp,
                )
            )
            chunk["path"] = rel
            chunk["outcome"] = "ok"
            items.append(chunk)
            remaining -= len(text)
        return finalize_sparse_document_payload(
            {"selection": "multi", "read_multi": True, "items": items}
        )

    if multi_expanded and has_section:
        return sparse_selector_conflict_error(
            "read_multi does not support a single section= selector across multiple paths"
        )

    results: dict[str, Any] = {}
    for path in paths:
        try:
            per_exp = _per_path_expected(path)
            result = read_file_result(
                path,
                root=root,
                binary=binary,
                section=section,
                sections=sections,
                export=export,
                chunk_cursor=chunk_cursor,
                chunk_cursor_provided=chunk_cursor_provided,
                expected_read_sha256=per_exp,
            )
            if multi_path:
                results[path] = result
            elif binary or result.get("auto_binary"):
                results[path] = result
            else:
                results[path] = _read_files_batch_value(result)
        except Exception as exc:
            results[path] = {"error": str(exc)}
    if not multi_path:
        return {"files": results}
    return finalize_sparse_document_payload(
        {"selection": "multi", "read_multi": True, "files": results}
    )
