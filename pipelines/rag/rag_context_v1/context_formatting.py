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
        content[len(heading_prefix) :] if heading_prefix and content.startswith(heading_prefix) else content
    )

    sections = [f"[Source: {display_label} | Last changed: {c['indexed_at']}]"]
    if include_section_heading and heading:
        sections.append(
            "[Section heading — retrieval hint only, not evidence]\n"
            f"{heading}"
        )
    sections.append(f"[Body evidence]\n{body_text}")
    return "\n".join(sections)


def format_context(
    chunks: list[ChunkData],
    *,
    include_section_headings: bool = False,
    include_source_titles: bool = False,
) -> str:
    """Format chunks for prompt injection as source blocks only.

    Source paths are normalized to filenames.  Chunks whose source extension
    is in _BINARY_EXTENSIONS are silently dropped.

    Contiguous chunks from the same source (adjacent chunk_index values) are
    merged into a single source block with overlap trimmed.

    Invariant: ∀ non-empty chunks list: returns non-empty string.
    """
    if not chunks:
        return _NO_RESULTS_SENTINEL

    filtered: list[ChunkData] = []

    for c in chunks:
        if source_is_binary(c["source"]):
            continue
        filtered.append(c)

    if not filtered:
        return _NO_RESULTS_SENTINEL

    filtered.sort(key=lambda c: (c["source"], c["metadata"].get("chunk_index", 0)))

    sections: list[str] = []
    i = 0
    while i < len(filtered):
        run_start = i
        source = filtered[i]["source"]
        run_text = filtered[i]["content"]

        while i + 1 < len(filtered):
            nxt = filtered[i + 1]
            cur_meta = filtered[i].get("metadata") or {}
            nxt_meta = nxt.get("metadata") or {}
            cur_idx = cur_meta.get("chunk_index")
            nxt_idx = nxt_meta.get("chunk_index")
            if (
                nxt["source"] != source
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

        first = filtered[run_start]
        merged_chunk: ChunkData = {
            "content": run_text,
            "source": first["source"],
            "indexed_at": first["indexed_at"],
            "metadata": first["metadata"],
            "content_hash": first.get("content_hash", ""),
            "score": first.get("score", 0.0),
        }
        label = normalize_source(source)
        sections.append(
            _format_source_line(
                label,
                merged_chunk,
                include_section_heading=include_section_headings,
                include_source_title=include_source_titles,
            )
        )
        i += 1

    return "\n\n---\n\n".join(sections)
