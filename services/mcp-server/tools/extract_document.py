"""``extract_document`` MCP tool — document ingestion redesign phase-c.

Writes a canonical sidecar markdown next to the source with YAML frontmatter
binding to ``source_sha256``. No entity creation, no graph writes — those
happen in ``promote_document_to_evidence`` after the operator has read the
sidecar and chosen ``entity_id``.

Spec: cortex://notes/system/specs/document-ingestion-redesign.md
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mcp_events import monotonic_now, record
from universal_logging import get_logger

from ._extract_document_helpers import (
    ALL_SUPPORTED_SUFFIXES,
    atomic_write,
    build_frontmatter,
    build_sidecar_path,
    check_idempotent,
    compute_source_sha256,
    detect_format,
    extract_text,
    format_sidecar,
)
from ._extraction_profile import hash_prompt, load_default_profile
from ._file_helpers import FILES_ROOT, resolve_files_path
from ._sidecar_naming import compute_args_hash, normalize_page_spec
from ._sidecar_schema import validate_sidecar_frontmatter

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = get_logger(__name__)


def register_extract_document_tools(mcp: FastMCP) -> None:
    """Register the ``extract_document`` tool on the MCP surface."""

    @mcp.tool(title="Extract Document")
    def extract_document(
        path: str,
        dpi: int = 0,
        pages: list[int] | None = None,
        prompt: str = "",
        model: str = "",
    ) -> dict[str, Any]:
        """Extract a document into a sidecar markdown with YAML frontmatter.

        Available via: dispatch(tool="extract_document", arguments='{"path": "..."}')

        Reads a source document, extracts text using a format-appropriate
        strategy (pymupdf4llm for text PDFs, vision OCR with auto-resize
        for scanned PDFs and images, python-docx / odfpy / email for rich
        formats), and writes a canonical sidecar markdown adjacent to the
        source with YAML frontmatter binding to ``source_sha256``.

        No entity creation, no graph writes — those happen in
        ``promote_document_to_evidence`` after the operator reads the
        sidecar and chooses ``entity_id``.

        Use when: you have a document in ``dropbox/<context>/<date>/`` and
        want readable text persisted so future reads skip OCR. Workflow:
        ``extract_document`` → read sidecar → ``promote_document_to_evidence``.

        Sidecar naming::

            <source-basename>[.pages-<spec>][.args-<6hex>].extracted.md

        Idempotent: identical re-invocation against an unchanged source
        returns the existing path without rewrite. Source SHA mismatch →
        auto-replace. ``page_spec`` or args-hash mismatch with filename
        collision → fail loudly.

        Args:
            path: File path relative to ``/data/files/``.
            dpi: Render resolution for PDF→image conversion. ``0`` (default)
                falls back to the pinned profile's value (currently 200).
                Used by scanned PDFs only.
            pages: Optional page selection for partial extraction. ``None``
                (default) → full extraction. Otherwise ``list[int]`` of
                1-based page numbers; sorted, deduplicated, and contiguous
                runs coalesced into the filename infix per spec §"Page spec
                normalization".
            prompt: Optional OCR prompt override. ``""`` (default) → profile
                default. Hashed into ``prompt_hash`` for both the filename
                ``.args-`` infix and the sidecar frontmatter.
            model: Optional model id override. ``""`` (default) → profile
                default.

        Returns:
            ``{path, sidecar_path, pages, model, total_tokens, canonical,
            partial}``. ``total_tokens`` is ``None`` for deterministic-parser
            paths (text PDFs, DOCX, ODT, EML, plain text). ``sidecar_path``
            is ``None`` when extraction returned empty text (no sidecar
            written; an explanatory ``warning`` is included).
        """
        t0 = monotonic_now()
        record("mcp.document.extract.called", path=path)

        # Resolve + validate input
        abs_path = resolve_files_path(path)
        if not abs_path.exists():
            raise FileNotFoundError(f"File not found: {path!r}")
        if not abs_path.is_file():
            raise ValueError(f"Not a file: {path!r}")

        suffix = abs_path.suffix.lower()
        if suffix not in ALL_SUPPORTED_SUFFIXES:
            raise ValueError(
                f"Unsupported format {suffix!r}. "
                f"Supported: {', '.join(sorted(ALL_SUPPORTED_SUFFIXES))}",
            )

        # Resolve effective args against the pinned default profile
        profile = load_default_profile()
        effective_dpi = dpi if dpi > 0 else profile.dpi
        effective_model = model if model else profile.model
        effective_prompt = prompt if prompt else profile.prompt
        effective_prompt_hash = hash_prompt(effective_prompt)

        # Compute page spec + args hash. total_pages is not passed to
        # normalize_page_spec here — a future enhancement may detect total
        # pages from the source PDF and auto-collapse covering lists into
        # canonical full extractions; for c.2, callers who want canonical
        # full extraction omit `pages` rather than passing the full list.
        page_spec = normalize_page_spec(pages)
        args_input = {
            "model": effective_model,
            "dpi": effective_dpi,
            "prompt_hash": effective_prompt_hash,
            "extraction_type": profile.extraction_type,
        }
        args_hash = compute_args_hash(args_input, profile.as_args_dict_for_hashing())

        # Source SHA + size — required for both frontmatter and idempotency
        source_sha256, source_size = compute_source_sha256(abs_path)

        # Compose sidecar path per the naming grammar
        sidecar_abs = build_sidecar_path(abs_path, page_spec, args_hash)
        sidecar_rel = str(sidecar_abs.relative_to(FILES_ROOT))

        canonical = page_spec.is_full and args_hash.full_hash is None
        partial = not page_spec.is_full

        # Idempotency check: existing sidecar with matching SHA + spec +
        # args → no rewrite. Collision (page_spec or args differ at
        # matched filename) raises inside check_idempotent.
        page_spec_str = "all" if page_spec.is_full else page_spec.filename_infix
        if check_idempotent(
            sidecar_abs,
            source_sha256=source_sha256,
            page_spec_str=page_spec_str,
            args_hash_full=args_hash.full_hash,
        ):
            record(
                "mcp.document.extract.idempotent",
                path=path,
                sidecar_path=sidecar_rel,
            )
            return {
                "path": path,
                "sidecar_path": sidecar_rel,
                "pages": page_spec.pages_for_frontmatter,
                "model": effective_model,
                "total_tokens": None,
                "canonical": canonical,
                "partial": partial,
            }

        # Extract text
        fmt = detect_format(abs_path)
        logger.info("extract_document: %s detected as %s", path, fmt)
        pages_for_extract = (
            page_spec.pages_for_frontmatter
            if isinstance(page_spec.pages_for_frontmatter, list)
            else None
        )
        text, total_tokens = extract_text(
            abs_path,
            fmt,
            dpi=effective_dpi,
            model=effective_model,
            prompt=effective_prompt,
            pages=pages_for_extract,
        )
        if not text.strip():
            record("mcp.document.extract.empty", path=path, format=fmt)
            return {
                "path": path,
                "sidecar_path": None,
                "pages": page_spec.pages_for_frontmatter,
                "model": effective_model,
                "total_tokens": total_tokens,
                "canonical": canonical,
                "partial": partial,
                "warning": "Extraction returned empty text — sidecar not written",
            }

        # Build + validate frontmatter (fail-closed at write-time per
        # spec §"Schema > Validation discipline > Write-time")
        frontmatter = build_frontmatter(
            source_path_rel=path,
            source_sha256=source_sha256,
            source_size=source_size,
            page_spec=page_spec,
            args_hash=args_hash,
            profile=profile,
            effective_model=effective_model,
            effective_dpi=effective_dpi,
            effective_prompt_hash=effective_prompt_hash,
        )
        validation = validate_sidecar_frontmatter(frontmatter)
        if not validation.ok:
            raise ValueError(
                f"Frontmatter failed schema validation; refusing to write "
                f"sidecar at {sidecar_rel}. Errors: {validation.errors}",
            )

        # Atomic write
        atomic_write(sidecar_abs, format_sidecar(frontmatter, text))

        elapsed = monotonic_now() - t0
        record(
            "mcp.document.extract.completed",
            path=path,
            format=fmt,
            sidecar_path=sidecar_rel,
            canonical=canonical,
            duration_s=round(elapsed, 3),
            total_tokens=total_tokens,
        )
        logger.info(
            "extract_document: %s → %s (%d chars, %.1fs)",
            path,
            sidecar_rel,
            len(text),
            elapsed,
        )
        return {
            "path": path,
            "sidecar_path": sidecar_rel,
            "pages": page_spec.pages_for_frontmatter,
            "model": effective_model,
            "total_tokens": total_tokens,
            "canonical": canonical,
            "partial": partial,
        }
