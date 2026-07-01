"""RAG proxy op — resolve assertion chunk_id to Stargate chunk text."""

from __future__ import annotations

from typing import Any

from universal_logging import get_logger

from ...rag_resolver import ChunkIdMismatchError, resolve_assertion_chunk
from .._shared import record

logger = get_logger("cortex-api.dispatch_ops.adapters.rag")


def _op_resolve_assertion_chunk(
    assertion_id: int | None = None,
    **_: object,
) -> dict[str, Any]:
    """Resolve an assertion's chunk_id to RAG chunk text."""
    if assertion_id is None:
        return {"error": "assertion_id is required (integer)"}
    try:
        chunk = resolve_assertion_chunk(int(assertion_id))
        record("mcp.cortex.resolve_assertion_chunk", assertion_id=assertion_id)
        return {
            "assertion_id": assertion_id,
            "chunk": chunk,
        }
    except ChunkIdMismatchError as exc:
        logger.error("resolve_assertion_chunk mismatch: %s", exc)
        return {
            "error": "chunk_id_mismatch",
            "detail": str(exc),
            "assertion_id": assertion_id,
        }
    except ValueError as exc:
        return {"error": str(exc), "assertion_id": assertion_id}
    except Exception as exc:
        logger.error("resolve_assertion_chunk failed: %s", exc)
        return {
            "error": f"RAG lookup failed: {exc}",
            "assertion_id": assertion_id,
        }


__all__ = ["_op_resolve_assertion_chunk"]
