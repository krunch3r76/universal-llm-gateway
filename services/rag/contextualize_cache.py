"""Pure planner and merger for the contextualize prefix cache.

Given chunk metadata and a dict of already-cached prefixes, this module
computes which chunks can reuse cached contexts and which must be
recomputed, merges recomputed results back into original order, and
filters non-empty computed prefixes into rows ready for persistence.

No I/O, no async — lives separately from rag_service/indexing.py so the
hot-path module stays focused on orchestration and event emission.

Empty-prefix invariant (referenced by V10 CHECK constraint):
contextualize_chunks() returns "" on per-chunk failure. Empty prefixes
MUST NOT enter the cache — they would become sticky false hits and
suppress legitimate retries forever. build_stored_context_rows() is the
single application-layer chokepoint enforcing this; the V10 CHECK
constraint is the storage-layer backstop.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from services.rag.contextualize import compute_neighbor_digest

if TYPE_CHECKING:
    from services.rag.chunkers import Chunk


def resolve_source_identity(source: str) -> str:
    """Return the stable path string used as G1 ``source_identity``."""
    return str(Path(source).expanduser().resolve())


def contextualize_cache_legacy_fallback_enabled() -> bool:
    """Return whether legacy V10 dual-read fallback is active (default on)."""
    raw = os.environ.get("CONTEXTUALIZE_CACHE_LEGACY_FALLBACK", "1")
    return raw.strip().lower() not in {"0", "false", "no"}


@dataclass(slots=True, kw_only=True, frozen=True)
class CacheMissChunk:
    """One chunk that missed the cache, with its original index, source chunk, and content hash."""

    index: int
    chunk: Chunk
    chunk_hash: str
    neighbor_digest: str


@dataclass(slots=True, kw_only=True)
class ContextCachePlan:
    """Plan describing cache hits and misses for one file's chunks before contextualization.

    contexts holds the cached prefix at hit positions and "" at miss positions.
    cache_misses is the input to contextualize_chunks() for the misses only.
    """

    contexts: list[str]
    cache_misses: list[CacheMissChunk]
    cache_hits: int

    @property
    def cache_misses_count(self) -> int:
        """Convenience accessor for the miss count, mirrored against cache_hits."""
        return len(self.cache_misses)


@dataclass(slots=True, kw_only=True, frozen=True)
class StoredContextRow:
    """One cache row to persist after successful contextualization."""

    chunk_hash: str
    context_prefix: str
    neighbor_digest: str


def build_context_cache_plan(
    *,
    chunks: list[Chunk],
    metadatas: list[dict[str, Any]],
    cached_contexts: dict[str, str],
) -> ContextCachePlan:
    """Build a reuse plan by matching chunk_hash metadata against cached prefixes."""
    if len(chunks) != len(metadatas):
        raise ValueError(
            f"chunks/metadatas length mismatch: {len(chunks)} vs {len(metadatas)}"
        )

    contexts: list[str] = [""] * len(chunks)
    cache_misses: list[CacheMissChunk] = []

    for index, (chunk, metadata) in enumerate(zip(chunks, metadatas, strict=True)):
        chunk_hash = str(metadata.get("chunk_hash", ""))
        cached_prefix = cached_contexts.get(chunk_hash) if chunk_hash else None
        neighbor_digest = compute_neighbor_digest(chunks, index)
        if cached_prefix:
            contexts[index] = cached_prefix
        else:
            cache_misses.append(
                CacheMissChunk(
                    index=index,
                    chunk=chunk,
                    chunk_hash=chunk_hash,
                    neighbor_digest=neighbor_digest,
                )
            )

    return ContextCachePlan(
        contexts=contexts,
        cache_misses=cache_misses,
        cache_hits=len(chunks) - len(cache_misses),
    )


def merge_computed_contexts(
    *,
    plan: ContextCachePlan,
    computed_prefixes: list[str],
) -> list[str]:
    """Return a new contexts list with recomputed prefixes merged into miss positions.

    Raises ValueError on length mismatch — silent truncation would corrupt
    the index by misassigning prefixes to wrong chunks. Returns a fresh
    list (does not mutate plan.contexts) so callers can keep the plan
    around for build_stored_context_rows().
    """
    if len(computed_prefixes) != len(plan.cache_misses):
        raise ValueError(
            "computed_prefixes / cache_misses length mismatch: "
            f"{len(computed_prefixes)} vs {len(plan.cache_misses)}"
        )

    contexts = list(plan.contexts)
    for miss, context_prefix in zip(plan.cache_misses, computed_prefixes, strict=True):
        contexts[miss.index] = context_prefix
    return contexts


def build_stored_context_rows(
    *,
    plan: ContextCachePlan,
    computed_prefixes: list[str],
) -> list[StoredContextRow]:
    """Build persistence rows for non-empty computed prefixes that have a non-empty chunk_hash.

    Skips empty prefixes (per-chunk failure marker — never persist) and
    skips empty chunk_hash (V10 CHECK constraint forbids empty key).
    Both filters are mandatory; the V10 CHECK is the storage-layer
    backstop for this same invariant.
    """
    if len(computed_prefixes) != len(plan.cache_misses):
        raise ValueError(
            "computed_prefixes / cache_misses length mismatch: "
            f"{len(computed_prefixes)} vs {len(plan.cache_misses)}"
        )

    rows: list[StoredContextRow] = []
    for miss, context_prefix in zip(plan.cache_misses, computed_prefixes, strict=True):
        if not context_prefix or not miss.chunk_hash:
            continue
        rows.append(
            StoredContextRow(
                chunk_hash=miss.chunk_hash,
                context_prefix=context_prefix,
                neighbor_digest=miss.neighbor_digest,
            )
        )
    return rows
