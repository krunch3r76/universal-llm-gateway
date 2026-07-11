"""Helpers for ``extract_document`` (phase-c.2).

Extraction orchestration: format detection, text-extract dispatch, sidecar
path composition, frontmatter build, atomic write, idempotency check, and
the source-SHA hashing used at both extract-time and promote-time.

Sidecar shape (validation, frontmatter parsing, suffix constant) lives in
``sidecar_schema``. Naming grammar (PageSpec, ArgsHash) lives in
``_sidecar_naming``. Default-profile load + ``hash_prompt`` live in
``extraction_profile``.

Spec: cortex://notes/system/specs/document-ingestion-redesign.md
"""

from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import yaml
from ocr_core import has_text_layer, ocr_pages

from ._extraction_profile import DefaultProfile
from ._sidecar_naming import ArgsHash, PageSpec
from ._sidecar_schema import SIDECAR_SUFFIX, parse_leading_frontmatter

STARGATE_URL = os.environ.get("STARGATE_URL", "http://io:9999")

# ─── Module-level constants ──────────────────────────────────────────────────


# Source formats accepted by extract_document.
_TEXT_EXTRACTABLE: Final[frozenset[str]] = frozenset(
    {".pdf", ".docx", ".odt", ".eml", ".html", ".txt", ".md"},
)
_IMAGE_SUFFIXES: Final[frozenset[str]] = frozenset(
    {".png", ".jpg", ".jpeg", ".tiff", ".tif", ".webp", ".bmp"},
)
ALL_SUPPORTED_SUFFIXES: Final[frozenset[str]] = _TEXT_EXTRACTABLE | _IMAGE_SUFFIXES

# Tool version embedded in sidecar frontmatter per spec §Frontmatter.
# Bump when the sidecar shape or extraction contract changes in a way that
# downstream readers need to distinguish.
TOOL_VERSION: Final[str] = "extract_document/1.0"


# ─── Source hashing ──────────────────────────────────────────────────────────


def compute_source_sha256(path: Path) -> tuple[str, int]:
    """Read a file and return ``(sha256_hex, size_bytes)``.

    Streams in 64KB chunks so large PDFs don't pin memory. Used at both
    extract-time (frontmatter binding, idempotency) and promote-time
    (re-verification + sidecar SHA in manifest).
    """
    hasher = hashlib.sha256()
    size = 0
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            hasher.update(chunk)
            size += len(chunk)
    return hasher.hexdigest(), size


# ─── Format detection + text extraction dispatch ─────────────────────────────


def detect_format(path: Path) -> str:
    """Classify a file into a processing strategy.

    Returns one of: ``"text_pdf"``, ``"scanned_pdf"``, ``"image"``,
    ``"rich_text"``, ``"plain_text"``.
    """
    suffix = path.suffix.lower()
    if suffix in _IMAGE_SUFFIXES:
        return "image"
    if suffix == ".pdf":
        if has_text_layer(path):
            return "text_pdf"
        return "scanned_pdf"
    if suffix in (".docx", ".odt", ".eml", ".html"):
        return "rich_text"
    return "plain_text"


def extract_text(
    path: Path,
    fmt: str,
    *,
    dpi: int,
    model: str,
    prompt: str,
    pages: list[int] | None,
) -> tuple[str, int | None]:
    """Extract text and report ``total_tokens`` (None for deterministic parsers).

    ``total_tokens`` is ``None`` whenever no model invocation occurred
    (text PDFs via pymupdf, DOCX via python-docx, ODT via odfpy, EML via
    email parser, plain text via direct read).
    """
    if fmt == "text_pdf":
        from ._file_helpers import read_pdf

        return read_pdf(path), None

    if fmt in ("scanned_pdf", "image"):
        kwargs: dict[str, Any] = {"stargate_url": STARGATE_URL, "dpi": dpi}
        if model:
            kwargs["model"] = model
        if prompt:
            kwargs["prompt"] = prompt
        if pages:
            kwargs["pages"] = pages
        result = ocr_pages(path, **kwargs)
        return result.get("text", ""), result.get("total_tokens")

    if fmt == "rich_text":
        suffix = path.suffix.lower()
        if suffix == ".docx":
            from ._file_helpers import read_docx

            return read_docx(path), None
        if suffix == ".odt":
            from ._file_helpers import read_odt

            return read_odt(path), None
        if suffix == ".eml":
            from ._file_helpers import read_eml

            return read_eml(path), None
        return path.read_text(encoding="utf-8", errors="replace"), None

    return path.read_text(encoding="utf-8", errors="replace"), None


# ─── Sidecar path composition + frontmatter build ────────────────────────────


def build_sidecar_path(
    source_path: Path,
    page_spec: PageSpec,
    args_hash: ArgsHash,
) -> Path:
    """Compose sidecar path per spec §"Naming grammar".

    Format: ``<source-basename>[.pages-<infix>][.args-<prefix>].extracted.md``

    Source basename includes the suffix (so ``file.pdf`` and ``file.png``
    get distinct sidecars). Disambiguators appear in fixed order: ``.pages-``
    first, then ``.args-``.
    """
    parts: list[str] = [source_path.name]
    if not page_spec.is_full:
        parts.append(f".pages-{page_spec.filename_infix}")
    if args_hash.prefix is not None:
        parts.append(f".args-{args_hash.prefix}")
    parts.append(SIDECAR_SUFFIX)
    return source_path.parent / "".join(parts)


def build_frontmatter(
    *,
    source_path_rel: str,
    source_sha256: str,
    source_size: int,
    page_spec: PageSpec,
    args_hash: ArgsHash,
    profile: DefaultProfile,
    effective_model: str,
    effective_dpi: int,
    effective_prompt_hash: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build the YAML frontmatter dict for a fresh sidecar.

    Field set matches ``cortex://configs/schemas/extraction-sidecar-v1.yaml``
    exactly; caller is expected to feed the result through
    ``validate_sidecar_frontmatter`` before write to satisfy spec
    §"Schema > Validation discipline > Write-time".
    """
    timestamp = now or datetime.now(UTC)
    return {
        "naming_version": 1,
        "canonical": page_spec.is_full and args_hash.full_hash is None,
        "partial": not page_spec.is_full,
        "page_spec": "all" if page_spec.is_full else page_spec.filename_infix,
        "args_hash": args_hash.full_hash,
        "args_hash_prefix": args_hash.prefix,
        "default_profile": profile.profile,
        "source_path": source_path_rel,
        "source_sha256": source_sha256,
        "source_size": source_size,
        "extracted_at": timestamp.isoformat(timespec="seconds").replace(
            "+00:00",
            "Z",
        ),
        "model": effective_model,
        "dpi": effective_dpi,
        "pages": page_spec.pages_for_frontmatter,
        "prompt_hash": effective_prompt_hash,
        "extraction_type": profile.extraction_type,
        "tool_version": TOOL_VERSION,
    }


def format_sidecar(frontmatter: dict[str, Any], body: str) -> str:
    """Serialize sidecar contents: opening ``---``, YAML block, closing ``---``, body."""
    yaml_block = yaml.safe_dump(
        frontmatter,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
    )
    return f"---\n{yaml_block}---\n\n{body}"


# ─── Atomic write + idempotency check ────────────────────────────────────────


def atomic_write(target: Path, content: str) -> None:
    """Write ``content`` to ``target`` atomically via ``.tmp`` + ``fsync`` + ``rename``.

    Spec §"Atomic sidecar writes": half-written sidecars MUST NOT become
    visible to readers. ``Path.replace`` is atomic on POSIX when source and
    target are on the same filesystem, which they are here (both inside
    ``target.parent``).
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        f.write(content)
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(target)


def check_idempotent(
    sidecar_path: Path,
    *,
    source_sha256: str,
    page_spec_str: str,
    args_hash_full: str | None,
) -> bool:
    """Return ``True`` iff an existing sidecar matches this call (no rewrite needed).

    Per spec §"Idempotency and collision handling":

    - ``source_sha256`` + ``page_spec`` + ``args_hash`` all match → ``True``.
    - ``source_sha256`` mismatch → source has changed; caller auto-replaces
      (returns ``False``; the caller path overwrites the stale sidecar).
    - Filename match but ``page_spec`` or ``args_hash`` mismatch → hash or
      schema collision; raise ``ValueError`` rather than overwrite.
    - Sidecar missing or malformed (parse failure / unexpected shape) →
      ``False`` so the caller writes a fresh sidecar.
    """
    if not sidecar_path.exists():
        return False

    try:
        with sidecar_path.open("r", encoding="utf-8") as f:
            content = f.read()
    except OSError:
        return False

    fm = parse_leading_frontmatter(content)
    if fm is None:
        return False

    existing_sha = fm.get("source_sha256")
    existing_page_spec = fm.get("page_spec")
    existing_args_hash = fm.get("args_hash")

    if existing_sha != source_sha256:
        # Source changed under us; caller auto-replaces. No collision raise.
        return False

    if existing_page_spec != page_spec_str:
        raise ValueError(
            f"Sidecar filename collision at {sidecar_path.name}: existing "
            f"page_spec {existing_page_spec!r} differs from current "
            f"{page_spec_str!r} despite identical filename. Investigate "
            f"before overwriting.",
        )
    if existing_args_hash != args_hash_full:
        raise ValueError(
            f"Sidecar filename collision at {sidecar_path.name}: existing "
            f"args_hash {existing_args_hash!r} differs from current "
            f"{args_hash_full!r} despite identical filename. Investigate "
            f"before overwriting.",
        )

    return True
