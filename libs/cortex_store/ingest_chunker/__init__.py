"""Chunkers for document ingestion, dispatched by ``authority_class``.

Per docs/architecture/entity-backed-claim-provenance.md § 3.2 each authority
class has a structurally-aware chunker — statutes split at leaf
subdivisions, opinions split at page+paragraph, agency letters at
section/Q&A, and so on. Phase 1 (spec § 9.1 item 7) ships only the
dispatch surface plus the ``subdivision_tree`` chunker (statute /
regulation / probate_code); other class chunkers are added per-class as
Phase 2 entities arrive.

The default chunker (paragraph-boundary token-bounded splitter) preserves
the pre-spec ``ingest_document`` behavior for callers that omit
``authority_class``.
"""

from __future__ import annotations

from collections.abc import Callable

from .chunk_spec import ChunkSpec
from .default import chunk_default
from .subdivision_tree import chunk_subdivision_tree

_Chunker = Callable[[str], list[ChunkSpec]]


_CHUNKER_BY_AUTHORITY_CLASS: dict[str, _Chunker] = {
    "statute": chunk_subdivision_tree,
    "regulation": chunk_subdivision_tree,
    "probate_code": chunk_subdivision_tree,
}


def chunk_for_authority(
    text: str, authority_class: str | None = None
) -> list[ChunkSpec]:
    """Chunk *text* using the chunker registered for *authority_class*.

    When *authority_class* is None or has no registered chunker, falls back
    to the default paragraph-boundary chunker. Phase 1 ships only the
    subdivision-tree chunker; other classes (`agency_letter`, `publication`,
    `annotation`, `treatise`, `model_rule`, `case-law`) fall through to the
    default until their per-class chunker is added in Phase 2.
    """
    if authority_class is None:
        return chunk_default(text)
    chunker = _CHUNKER_BY_AUTHORITY_CLASS.get(authority_class)
    if chunker is None:
        return chunk_default(text)
    return chunker(text)


__all__ = ["ChunkSpec", "chunk_for_authority"]
