"""Embed-phase diff gate: content-addressed chunk IDs and skip partition helpers."""

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
    """Return positional content hash (unchanged from pre-S1 scheme)."""
    material = f"{chunk_index}|{text}".encode()
    return hashlib.sha256(material).hexdigest()[:16]


def compose_chunk_id(path_key: str, chunk_hash: str) -> str:
    """Compose a content-addressed chunk ID: ``{path_key}-{chunk_hash}``."""
    return f"{path_key}-{chunk_hash}"


def is_legacy_chunk_id(chunk_id: str) -> bool:
    """Return True when ``chunk_id`` matches legacy ``{16hex}-{decimal}`` scheme."""
    return bool(LEGACY_CHUNK_ID_RE.match(chunk_id))


def is_new_scheme_chunk_id(chunk_id: str) -> bool:
    """Return True when ``chunk_id`` matches new ``{16hex}-{16hex}`` scheme."""
    return bool(NEW_CHUNK_ID_RE.match(chunk_id))


def count_legacy_chunk_ids(ids: list[str]) -> int:
    """Count existing IDs still on the legacy whole-file-prefix scheme."""
    return sum(1 for chunk_id in ids if is_legacy_chunk_id(chunk_id))


def cache_hit_flags_from_miss_indices(
    chunk_count: int, cache_miss_indices: set[int]
) -> list[bool]:
    """Build per-chunk cache-hit booleans from contextualize miss indices."""
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
    """Return items at the given indices preserving order."""
    return [items[index] for index in indices]


def compute_stale_ids(existing_ids: list[str], new_ids: list[str]) -> list[str]:
    """Compute stale IDs from the full new ID set (never from processed subset)."""
    return list(set(existing_ids) - set(new_ids))
