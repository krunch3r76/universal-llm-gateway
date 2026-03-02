from __future__ import annotations

import hashlib

import chromadb

from services.rag.models import IndexResult


def file_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def all_ids_match_prefix(ids: list[str], prefix: str) -> bool:
    return bool(ids) and all(item_id.startswith(f"{prefix}-") for item_id in ids)


def check_pdf_duplicate(
    collection: chromadb.Collection,
    pdf_hash: str,
    source: str,
) -> IndexResult | None:
    try:
        existing = collection.get(
            where={"pdf_hash": pdf_hash},
            include=["metadatas"],
            limit=1,
        )
    except Exception:
        return None
    for metadata in existing.get("metadatas") or []:
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
