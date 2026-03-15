from __future__ import annotations

import hashlib
import logging

import chromadb

from services.rag.models import IndexResult

logger = logging.getLogger(__name__)


def file_hash(data: bytes, schema_version: int = 0) -> str:
    """Hash file content, incorporating extraction schema version when > 0.

    A schema_version bump makes all existing hashes stale, forcing
    re-extraction without manual reindex.
    """
    if schema_version > 0:
        data = data + f"__extraction_v{schema_version}".encode()
    return hashlib.sha256(data).hexdigest()


def all_ids_match_prefix(ids: list[str], prefix: str) -> bool:
    return bool(ids) and all(item_id.startswith(f"{prefix}-") for item_id in ids)


def check_pdf_duplicate(
    collection: chromadb.Collection,
    source_hash: str,
    source: str,
) -> IndexResult | None:
    try:
        existing = collection.get(
            where={"source_hash": source_hash},
            include=["metadatas"],
            limit=1,
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
