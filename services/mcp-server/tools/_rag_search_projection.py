"""Compact projection for ``rag_search`` envelopes (KERNEL-H / hop byte budget).

Full assembled ``context`` from ``rag-search`` can exceed 5 KB; ``projection=compact``
returns a bounded envelope (≤4500 UTF-8 bytes of JSON) with status, ``search_id``,
``weak_match``, ``top_relevance``, and a truncated ``context_preview``. Full chunk
text remains on ``projection=full`` (default).
"""

from __future__ import annotations

import json
from typing import Any

COMPACT_MAX_BYTES = 4500
_DEFAULT_SNIPPET_CHARS = 300


def _json_byte_length(envelope: dict[str, Any]) -> int:
    return len(
        json.dumps(envelope, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )


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

    snippet = full_context[:_DEFAULT_SNIPPET_CHARS]
    if len(full_context) > _DEFAULT_SNIPPET_CHARS:
        snippet += "..."
    projected["context_preview"] = snippet

    while _json_byte_length(projected) > COMPACT_MAX_BYTES and projected[
        "context_preview"
    ]:
        preview = projected["context_preview"]
        projected["context_preview"] = preview[: max(0, len(preview) - 128)]

    retrieval = projected.get("retrieval")
    if (
        _json_byte_length(projected) > COMPACT_MAX_BYTES
        and isinstance(retrieval, dict)
        and isinstance(retrieval.get("chunks"), list)
    ):
        trimmed = dict(retrieval)
        chunks = trimmed["chunks"]
        while chunks and _json_byte_length(projected) > COMPACT_MAX_BYTES:
            chunks = chunks[:-1]
            trimmed["chunks"] = chunks
            projected["retrieval"] = trimmed

    if _json_byte_length(projected) > COMPACT_MAX_BYTES:
        projected.pop("context_preview", None)

    return projected
