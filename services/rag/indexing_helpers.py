"""ChromaDB helper functions shared by the RAG file-indexing path.

Provides source-path migration for moved files (``migrate_chroma_source``),
schema-versioned content hashing (``file_hash``), chunk-id prefix checks and
PDF duplicate detection by ``source_hash`` (``check_pdf_duplicate``).
``rag_service.indexing.file_guards`` uses the migration and duplicate helpers
before normal indexing so moved or duplicated files are not re-embedded.
"""

from __future__ import annotations

import hashlib
import logging

import chromadb

from services.rag.models import IndexResult

logger = logging.getLogger(__name__)


def migrate_chroma_source(
    collection: chromadb.Collection,
    source_hash: str,
    old_source: str,
    new_source: str,
) -> int:
    """Update Chroma chunk metadata from old_source to new_source.

    Fetches all chunks matching ``old_source``, filters those with
    ``source == old_source``, and updates their source field to ``new_source``
    in a single collection.update() call.
    Returns the number of chunks updated; 0 when no matching chunks exist.
    """
    existing = collection.get(
        where={"source": old_source},
        include=["metadatas"],
    )
    ids_to_update = []
    metadatas_to_update = []
    for chunk_id, metadata in zip(
        existing.get("ids") or [], existing.get("metadatas") or [], strict=True
    ):
        if isinstance(metadata, dict) and metadata.get("source") == old_source:
            ids_to_update.append(chunk_id)
            metadatas_to_update.append({**metadata, "source": new_source})
    if not ids_to_update:
        return 0
    collection.update(ids=ids_to_update, metadatas=metadatas_to_update)
    return len(ids_to_update)


def file_hash(data: bytes, schema_version: int = 0) -> str:
    """Hash file content, incorporating extraction schema version when > 0.

    A schema_version bump makes all existing hashes stale, forcing
    re-extraction without manual reindex.
    """
    if schema_version > 0:
        data = data + f"__extraction_v{schema_version}".encode()
    return hashlib.sha256(data).hexdigest()


def all_ids_match_prefix(ids: list[str], prefix: str) -> bool:
    """Report whether every chunk id begins with ``<prefix>-`` (dash-delimited).

    Returns False for an empty list, so "no ids" is never treated as a match.
    Pure check with no ChromaDB access.
    """
    return bool(ids) and all(item_id.startswith(f"{prefix}-") for item_id in ids)


def check_pdf_duplicate(
    collection: chromadb.Collection,
    source_hash: str,
    source: str,
) -> IndexResult | None:
    """Detect a PDF whose content hash is already indexed under another path.

    Queries up to 10 chunks with matching ``source_hash``; if any belongs to a
    different ``source``, returns an unchanged ``IndexResult`` with
    ``duplicate=True`` and ``duplicate_of`` set so indexing is skipped. Returns
    None when no duplicate exists or when the ChromaDB query fails (logged).
    """
    try:
        existing = collection.get(
            where={"source_hash": source_hash},
            include=["metadatas"],
            limit=10,
        )
    except chromadb.errors.ChromaError as e:
        logger.warning(
            "Failed to query ChromaDB for PDF duplicate check: %s", e, exc_info=True
        )
        return None
    raw_metadatas = existing.get("metadatas")
    metadatas = raw_metadatas if isinstance(raw_metadatas, list) else []
    for metadata in metadatas:
        if isinstance(metadata, dict):
            existing_source = metadata.get("source")
            if isinstance(existing_source, str) and existing_source != source:
                return IndexResult(
                    deleted=0,
                    indexed=0,
                    unchanged=True,
                    file=source,
                    duplicate=True,
                    duplicate_of=existing_source,
                )
    return None
