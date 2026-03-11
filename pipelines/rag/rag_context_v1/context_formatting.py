"""Context formatting for RAG retrieval output.

Formats retrieved chunks into prompt-ready context text with entity/relation/topic
sections.  Shared by both ``rag_multi_retrieve_v1`` and ``rag_rerank_assemble_v1``
handlers so formatting logic is not duplicated.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, TypedDict
from urllib.parse import urlparse

from systems.pipeline.core.constants import (
    RAG_NO_RESULTS_SENTINEL as _NO_RESULTS_SENTINEL,
)
from universal_logging import get_logger

from services.rag.entity_merging import (
    extract_entities_from_metadata,
    extract_topics_from_metadata,
    format_entity_context,
    format_relation_context,
    format_topic_context,
    merge_entities,
    merge_relations,
    merge_topics,
)
from services.rag.knowledge_extractor import Entity

logger = get_logger(__name__)


class ChunkData(TypedDict):
    """Serialized chunk for inter-step transfer."""

    content: str
    source: str
    indexed_at: str
    metadata: dict[str, Any]
    content_hash: str
    score: float


_BINARY_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".bin",
        ".gguf",
        ".ggml",
        ".pkl",
        ".pickle",
        ".pt",
        ".pth",
        ".ckpt",
        ".safetensors",
        ".npz",
        ".npy",
        ".zip",
        ".tar",
        ".gz",
        ".bz2",
        ".xz",
        ".7z",
        ".rar",
        ".jpg",
        ".jpeg",
        ".png",
        ".gif",
        ".webp",
        ".bmp",
        ".ico",
        ".tiff",
        ".so",
        ".dylib",
        ".dll",
        ".exe",
        ".whl",
        ".pyc",
        ".pyo",
        ".docx",
        ".xlsx",
        ".pptx",
        ".odt",
        ".ods",
    }
)


def normalize_source(source: str) -> str:
    """Return a short human-readable label for a chunk source.

    File paths -> basename (e.g. 'pipeline-system.md').
    URLs -> path basename if present, else netloc.
    Empty or unparseable -> 'unknown'.
    """
    if not source or source == "unknown":
        return "unknown"
    if "://" in source:
        parsed = urlparse(source)
        if parsed.path and parsed.path != "/":
            return Path(parsed.path).name
        return parsed.netloc or "unknown"
    return Path(source).name or "unknown"


def source_is_binary(source: str) -> bool:
    """Return True when the source extension is in the binary blocklist."""
    ext = Path(normalize_source(source)).suffix.lower()
    return bool(ext) and ext in _BINARY_EXTENSIONS


def _format_source_line(label: str, c: ChunkData) -> str:
    """Format a single chunk as a source line with optional article title and metadata."""
    meta = c.get("metadata") or {}
    title = (meta.get("article_title") or "").strip()
    if title:
        raw = [meta.get("article_authors"), meta.get("published_date")]
        parts = [str(p).strip() for p in raw if p is not None and str(p).strip()]
        if parts:
            title = f"{title} ({', '.join(parts)})"
        display_label = title
    else:
        display_label = label
    return (
        f"[Source: {display_label} | Last changed: {c['indexed_at']}]\n{c['content']}"
    )


def format_context(chunks: list[ChunkData]) -> str:
    """Format chunks for prompt injection with entity/relation/topic sections.

    Source paths are normalized to filenames.  Chunks whose source extension
    is in _BINARY_EXTENSIONS are silently dropped.

    When extraction metadata is present, merged entities, relations, and topics
    are appended as structured sections after the source chunks.

    Invariant: ∀ non-empty chunks list: returns non-empty string.
    """
    if not chunks:
        return _NO_RESULTS_SENTINEL

    accepted: list[tuple[str, ChunkData]] = []
    all_entities: list[Entity] = []
    all_topics: list[str] = []

    for c in chunks:
        if source_is_binary(c["source"]):
            logger.debug("format_context: dropped binary source '%s'", c["source"])
            continue
        accepted.append((normalize_source(c["source"]), c))
        all_entities.extend(extract_entities_from_metadata(c["metadata"]))
        all_topics.extend(extract_topics_from_metadata(c["metadata"]))

    if not accepted:
        return _NO_RESULTS_SENTINEL

    sections = [_format_source_line(label, c) for label, c in accepted]

    if all_entities:
        merged_entities = merge_entities(all_entities)
        entity_section = format_entity_context(merged_entities)
        if entity_section:
            logger.debug(
                "format_context: appended %d merged entities", len(merged_entities)
            )
            sections.append(entity_section)

        merged_relations = merge_relations(all_entities)
        relation_section = format_relation_context(merged_relations)
        if relation_section:
            sections.append(relation_section)

    if all_topics:
        merged_topics = merge_topics(all_topics)
        topic_section = format_topic_context(merged_topics)
        if topic_section:
            sections.append(topic_section)

    return "\n\n---\n\n".join(sections)
