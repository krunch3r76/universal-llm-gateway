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
    if not source:
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


def _format_source_line(
    label: str,
    c: ChunkData,
    *,
    include_section_heading: bool,
    include_source_title: bool,
) -> str:
    """Format a single chunk with source label and body text."""
    meta = c.get("metadata") or {}
    title = (meta.get("article_title") or "").strip()
    if include_source_title and title:
        raw = [meta.get("article_authors"), meta.get("published_date")]
        parts = [str(p).strip() for p in raw if p and str(p).strip()]
        if parts:
            title = f"{title} ({', '.join(parts)})"
        display_label = title
    else:
        display_label = label
    content = c["content"]
    heading = str(meta.get("heading") or meta.get("section_path") or "").strip()
    heading_prefix = f"## {heading}\n\n" if heading else ""
    body_text = (
        content[len(heading_prefix) :]
        if heading_prefix and content.startswith(heading_prefix)
        else content
    )

    sections = [f"[Source: {display_label} | Last changed: {c['indexed_at']}]"]
    if include_section_heading and heading:
        sections.append(
            f"[Section heading — retrieval hint only, not evidence]\n{heading}"
        )
    sections.append(f"[Body evidence]\n{body_text}")
    return "\n".join(sections)


def merge_adjacent_chunks(chunks: list[ChunkData]) -> list[ChunkData]:
    """Merge consecutive same-source chunks with adjacent indices.

    Walks *chunks* in their given order (rank order after reranking, retrieval
    order otherwise).  When two neighbors share the same source **and**
    ``section_path`` **and** have consecutive ``chunk_index`` values, they are
    collapsed into a single chunk with overlap trimmed.  The merged chunk
    inherits the metadata (and score) of the first chunk in the run.

    Chunks from the same source that are **not** consecutive in the list are
    left separate — they occupy independent rank positions.
    """
    if not chunks:
        return []

    merged: list[ChunkData] = []
    i = 0
    while i < len(chunks):
        first = chunks[i]
        run_text = first["content"]

        while i + 1 < len(chunks):
            cur_meta = chunks[i].get("metadata") or {}
            nxt = chunks[i + 1]
            nxt_meta = nxt.get("metadata") or {}
            cur_idx = cur_meta.get("chunk_index")
            nxt_idx = nxt_meta.get("chunk_index")
            if (
                nxt["source"] != first["source"]
                or cur_meta.get("section_path") != nxt_meta.get("section_path")
                or cur_idx is None
                or nxt_idx is None
                or int(nxt_idx) != int(cur_idx) + 1
            ):
                break
            overlap_len = int(nxt_meta.get("overlap_prefix_len", 0))
            overlap_len = min(overlap_len, len(nxt["content"]))
            continuation = nxt["content"][overlap_len:]
            if continuation:
                run_text += "\n\n" + continuation
            i += 1

        merged.append(
            {
                "content": run_text,
                "source": first["source"],
                "indexed_at": first["indexed_at"],
                "metadata": first["metadata"],
                "content_hash": first.get("content_hash", ""),
                "score": first.get("score", 0.0),
            }
        )
        i += 1

    return merged


def format_context(
    chunks: list[ChunkData],
    *,
    include_section_headings: bool = False,
    include_source_titles: bool = False,
) -> str:
    """Render pre-ordered, pre-merged chunks as prompt-ready source blocks.

    Pure rendering function — receives chunks in the order they should appear
    (rank order from reranker, retrieval order otherwise) and emits formatted
    text.  Binary-extension sources are silently dropped.

    Callers are responsible for merging adjacent chunks via
    ``merge_adjacent_chunks()`` before calling this function.

    Invariant: ∀ non-empty chunks list: returns non-empty string.
    """
    if not chunks:
        return _NO_RESULTS_SENTINEL

    sections: list[str] = []
    for c in chunks:
        if source_is_binary(c["source"]):
            continue
        label = normalize_source(c["source"])
        sections.append(
            _format_source_line(
                label,
                c,
                include_section_heading=include_section_headings,
                include_source_title=include_source_titles,
            )
        )

    return "\n\n---\n\n".join(sections) if sections else _NO_RESULTS_SENTINEL
