"""Embed-phase diff gate: content-addressed chunk IDs and skip partition helpers.

Pure functions used by ``indexing/embed.py``, ``indexing/contextualize.py`` and
``indexing/file_guards.py``. Chunk ids have the form ``{path_key}-{chunk_hash}``
(two 16-hex sha256 prefixes); the legacy ``{16hex}-{decimal}`` scheme is still
detected so unchanged-skip logic forces a rewrite. ``partition_embed_work``
decides which chunks need embedding, Chroma upsert and FTS work: a chunk is
skipped only when its id already exists and its contextualize cache hit.
Invariant: stale ids are always computed from the full new id set, never the
processed subset.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

LEGACY_CHUNK_ID_RE = re.compile(r"^[0-9a-f]{16}-\d+$")
NEW_CHUNK_ID_RE = re.compile(r"^[0-9a-f]{16}-[0-9a-f]{16}$")


def compute_path_key(source: str) -> str:
    """Return stable 16-hex path identity for chunk ID composition (B1)."""
    resolved = str(Path(source).expanduser().resolve())
    return hashlib.sha256(resolved.encode()).hexdigest()[:16]


def compute_chunk_hash(chunk_index: int, text: str) -> str:
    """Return positional content hash (unchanged from pre-S1 scheme).

    First 16 hex chars of sha256 over ``"{chunk_index}|{text}"``. Because the
    index is included, identical text at a different position hashes differently.
    ``embed.py`` stores it as ``chunk_hash`` metadata and as the id suffix.
    """
    material = f"{chunk_index}|{text}".encode()
    return hashlib.sha256(material).hexdigest()[:16]


def compose_chunk_id(path_key: str, chunk_hash: str) -> str:
    """Compose a content-addressed chunk ID: ``{path_key}-{chunk_hash}``.

    ``path_key`` comes from ``compute_path_key`` and ``chunk_hash`` from
    ``compute_chunk_hash``; the result matches ``NEW_CHUNK_ID_RE``. Used by
    ``embed.py`` to build Chroma and FTS ids.
    """
    return f"{path_key}-{chunk_hash}"


def is_legacy_chunk_id(chunk_id: str) -> bool:
    """Return True when ``chunk_id`` matches legacy ``{16hex}-{decimal}`` scheme.

    Legacy ids used a whole-file prefix plus chunk ordinal. ``file_guards`` uses
    this to refuse the unchanged-content skip so such sources get rewritten.
    """
    return bool(LEGACY_CHUNK_ID_RE.match(chunk_id))


def is_new_scheme_chunk_id(chunk_id: str) -> bool:
    """Return True when ``chunk_id`` matches new ``{16hex}-{16hex}`` scheme.

    The new scheme is the content-addressed ``{path_key}-{chunk_hash}`` form
    produced by ``compose_chunk_id``; the check is a full-string regex match.
    """
    return bool(NEW_CHUNK_ID_RE.match(chunk_id))


def count_legacy_chunk_ids(ids: list[str]) -> int:
    """Count existing IDs still on the legacy whole-file-prefix scheme.

    Feeds ``EmbedDiffPartition.legacy_id_count`` in ``partition_embed_work`` so
    embed diff events can report how many legacy ids a source still carries.
    """
    return sum(1 for chunk_id in ids if is_legacy_chunk_id(chunk_id))


def cache_hit_flags_from_miss_indices(
    chunk_count: int, cache_miss_indices: set[int]
) -> list[bool]:
    """Build per-chunk cache-hit booleans from contextualize miss indices.

    Returns a list of length ``chunk_count`` where position i is True unless i
    is in ``cache_miss_indices``. Produced by the contextualization phase and
    consumed by ``partition_embed_work`` to decide skip eligibility.
    """
    return [index not in cache_miss_indices for index in range(chunk_count)]


def should_skip_embed_upsert_fts(
    *,
    chunk_id: str,
    existing_ids: set[str],
    contextualize_cache_hit: bool,
) -> bool:
    """Return True when embed, Chroma upsert, and FTS may be skipped (B3)."""
    return chunk_id in existing_ids and contextualize_cache_hit


@dataclass(slots=True, frozen=True)
class EmbedDiffPartition:
    """Partition of chunks into skip vs process lists for embed/upsert/FTS."""

    processed_indices: list[int]
    skipped_count: int
    processed_count: int
    legacy_id_count: int


def partition_embed_work(
    *,
    ids: list[str],
    existing_ids: list[str],
    cache_hit_flags: list[bool],
) -> EmbedDiffPartition:
    """Select processed indices; skipped chunks retain prior store rows (B11)."""
    existing_set = set(existing_ids)
    processed_indices: list[int] = []
    skipped = 0
    for index, (chunk_id, cache_hit) in enumerate(
        zip(ids, cache_hit_flags, strict=True)
    ):
        if should_skip_embed_upsert_fts(
            chunk_id=chunk_id,
            existing_ids=existing_set,
            contextualize_cache_hit=cache_hit,
        ):
            skipped += 1
        else:
            processed_indices.append(index)
    return EmbedDiffPartition(
        processed_indices=processed_indices,
        skipped_count=skipped,
        processed_count=len(processed_indices),
        legacy_id_count=count_legacy_chunk_ids(existing_ids),
    )


def subset_by_indices[T](items: list[T], indices: list[int]) -> list[T]:
    """Return items at the given indices, in the order the indices are listed.

    Used by ``embed.py`` to slice ids, texts, embed texts and metadatas down to
    ``EmbedDiffPartition.processed_indices``. Raises IndexError on an index out
    of range; duplicate indices yield duplicate items.
    """
    return [items[index] for index in indices]


def compute_stale_ids(existing_ids: list[str], new_ids: list[str]) -> list[str]:
    """Compute stale IDs from the full new ID set (never from processed subset)."""
    return list(set(existing_ids) - set(new_ids))
