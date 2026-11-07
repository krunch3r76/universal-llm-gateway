"""Shared helpers for sandboxed file reads under /data/files."""

from __future__ import annotations

import base64
import mimetypes
from pathlib import Path
from typing import Any

from mcp_events import record

from ._pdf_read import (
    PDF_LAYOUT_MAX_BYTES as PDF_LAYOUT_MAX_BYTES,
)
from ._pdf_read import (
    PDF_METHOD_LAYOUT as PDF_METHOD_LAYOUT,
)
from ._pdf_read import (
    PDF_METHOD_PLAINTEXT_EMPTY as PDF_METHOD_PLAINTEXT_EMPTY,
)
from ._pdf_read import (
    PDF_METHOD_PLAINTEXT_GATED as PDF_METHOD_PLAINTEXT_GATED,
)
from ._pdf_read import (
    PDF_METHOD_PLAINTEXT_TIMEOUT as PDF_METHOD_PLAINTEXT_TIMEOUT,
)
from ._pdf_read import (
    PDF_METHOD_SIDECAR,
    _read_pdf_text,
    extract_pdf_plaintext_with_timeout,
    read_pdf,
)
from ._pdf_read import (
    PDF_READ_TIMEOUT_S as PDF_READ_TIMEOUT_S,
)
from ._sparse_document_read import (
    BATCH_MAX_CONTENT_CHARS,
    is_sparse_document_suffix,
)

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


def _stopped_multi_section_item(rel: str, sid: str, *, root: Path) -> dict[str, Any]:
    """Later section after the shared budget stopped.

    Content is not returned. ``oversize`` is set only when this id's own
    body exceeds the cap.
    """
    item: dict[str, Any] = {"path": rel, "id": sid, "outcome": "over_budget"}
    one = read_file_result(rel, root=root, binary=False, sections=[sid])
    probed = (one.get("items") or [None])[0]
    if isinstance(probed, dict) and probed.get("oversize") is True:
        item["oversize"] = True
        if "char_count" in probed:
            item["char_count"] = probed["char_count"]
        fetch = probed.get("fetch")
        item["fetch"] = (
            fetch
            if isinstance(fetch, dict)
            else {
                "section": sid,
                "chunk_cursor": 0,
            }
        )
    return item


def _stopped_export_item(rel: str, *, root: Path) -> dict[str, Any]:
    """Later document export path after the shared budget stopped.

    A path whose own text exceeds the cap carries ``oversize``. A path that
    is over budget only because an earlier path stopped the batch does not.
    Non-documents are not stopped here and do not receive an export fetch.
    """
    item: dict[str, Any] = {"path": rel, "outcome": "over_budget"}
    if not _is_sparse_path(rel, root):
        return item
    item["fetch"] = {
        "op": "read",
        "path": rel,
        "export": True,
        "chunk_cursor": 0,
    }
    length = len(_load_plain_text_for_sparse(rel, root))
    if length > BATCH_MAX_CONTENT_CHARS:
        item["oversize"] = True
        item["char_count"] = length
    return item


def _nondocument_export_item(rel: str, *, root: Path) -> dict[str, Any]:
    """One ordinary path inside a read_multi export.

    Text stays the full file body. A binary read keeps the no-selector
    envelope (``content_base64``, mime, hash) and never grows an export fetch
    or an empty ``content`` string.
    """
    probed = read_file_result(rel, root=root, binary=False)
    if (
        probed.get("auto_binary")
        or probed.get("is_binary")
        or "content_base64" in probed
    ):
        item = dict(probed)
        item["path"] = rel
        item.pop("fetch", None)
        item.pop("content", None)
        item["outcome"] = "ok"
        return item
    if probed.get("unwindowed_oversized") or probed.get("byte_truncated"):
        body = extract_text_content(resolve_files_path(rel, root=root))
    else:
        raw_body = probed.get("content")
        body = raw_body if isinstance(raw_body, str) else ""
    return {"path": rel, "content": body, "outcome": "ok"}


# Imported after the helpers they call, so the cycle resolves. Explicit
# aliases re-export the public read API from this module.
from ._file_read_result import read_file_result as read_file_result  # noqa: E402
from ._read_files_batch import read_files_batch as read_files_batch  # noqa: E402
