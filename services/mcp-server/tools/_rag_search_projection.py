"""Compact projection for ``rag_search`` envelopes (KERNEL-H / hop byte budget).

Full assembled ``context`` from ``rag-search`` can exceed 5 KB; ``projection=compact``
returns a bounded envelope (≤4500 UTF-8 bytes of JSON) with status, ``search_id``,
``weak_match``, ``top_relevance``, and per-chunk ``context_preview`` (top three chunks).
Full chunk text remains on ``projection=full`` (default).
"""

from __future__ import annotations

import json
from typing import Any

COMPACT_MAX_BYTES = 4500
_CHUNK_SEPARATOR = "\n\n---\n\n"
_PREVIEW_CHUNK_COUNT = 3
_PREVIEW_CHARS_PER_CHUNK = 350
_PREVIEW_SHRINK_STEP = 128

def _json_byte_length(envelope: dict[str, Any]) -> int:
    return len(
        json.dumps(envelope, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )


def _split_context_chunks(context: str) -> list[str]:
    if not context.strip():
        return []
    return context.split(_CHUNK_SEPARATOR)


def _chunk_preview(chunk: str, *, max_chars: int = _PREVIEW_CHARS_PER_CHUNK) -> str:
    """Source header line plus body after ``[Body evidence]``, capped at *max_chars*."""
    lines = chunk.splitlines()
    source_line = ""
    for line in lines:
        if line.startswith("[Source:"):
            source_line = line.strip()
            break

    body_parts: list[str] = []
    in_body = False
    for line in lines:
        stripped = line.strip()
        if stripped == "[Body evidence]":
            in_body = True
            continue
        if in_body:
            if stripped:
                body_parts.append(stripped)
            continue
        if source_line and stripped and not stripped.startswith("["):
            body_parts.append(stripped)

    body = " ".join(body_parts).strip()
    if source_line and body:
        preview = f"{source_line}\n{body}"
    elif source_line:
        preview = source_line
    elif body:
        preview = body
    else:
        preview = chunk.strip()

    if len(preview) > max_chars:
        preview = preview[: max(0, max_chars - 3)] + "..."
    return preview


def _build_context_preview(full_context: str) -> str:
    chunks = _split_context_chunks(full_context)[:_PREVIEW_CHUNK_COUNT]
    previews = [_chunk_preview(chunk) for chunk in chunks if chunk.strip()]
    return _CHUNK_SEPARATOR.join(previews)


def _trim_tail_chunk_rows(
    projected: dict[str, Any],
) -> int:
    """Drop tail ``retrieval.chunks[]`` rows until JSON fits or the list is empty."""
    retrieval = projected.get("retrieval")
    if not isinstance(retrieval, dict):
        return 0
    chunks = retrieval.get("chunks")
    if not isinstance(chunks, list) or not chunks:
        return 0

    trimmed = dict(retrieval)
    working = list(chunks)
    dropped = 0
    while working and _json_byte_length(projected) > COMPACT_MAX_BYTES:
        working.pop()
        dropped += 1
        trimmed["chunks"] = working
        projected["retrieval"] = trimmed
    return dropped


def project_rag_search_envelope(
    envelope: dict[str, Any],
    *,
    projection: str | None,
) -> dict[str, Any]:
    """Return *envelope* unchanged for ``full``; compact omits full ``context``."""
    mode = (projection or "full").strip().lower()
    if mode != "compact" or not isinstance(envelope, dict):
        return envelope
    if envelope.get("status") == "in_flight":
        return envelope

    full_context = envelope.get("context")
    if not isinstance(full_context, str):
        return envelope

    projected: dict[str, Any] = {
        key: value for key, value in envelope.items() if key != "context"
    }
    projected["projection"] = "compact"
    projected["projection_available"] = "full"
    if "content_length" not in projected:
        projected["content_length"] = len(full_context)

    projected["context_preview"] = _build_context_preview(full_context)

    chunks_truncated = _trim_tail_chunk_rows(projected)
    if chunks_truncated:
        projected["chunks_truncated"] = chunks_truncated

    while _json_byte_length(projected) > COMPACT_MAX_BYTES and projected.get(
        "context_preview"
    ):
        preview = projected["context_preview"]
        projected["context_preview"] = preview[: max(0, len(preview) - _PREVIEW_SHRINK_STEP)]

    if _json_byte_length(projected) > COMPACT_MAX_BYTES:
        projected.pop("context_preview", None)

    return projected
