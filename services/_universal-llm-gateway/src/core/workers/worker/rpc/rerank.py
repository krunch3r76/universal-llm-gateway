"""RPC handlers for cross-encoder reranking."""

import asyncio
from typing import Any

from universal_logging import get_logger
from universal_protocol.errors import EngineError

logger = get_logger(__name__)


class RerankHandlers:
    """RPC handlers for reranking (mixin for Worker class)."""

    async def handle_rerank(self, params: dict[str, Any]) -> dict[str, Any]:
        """Handle rerank RPC for cross-encoder scoring."""
        if not self.engine or not self.engine.is_loaded():
            raise EngineError(code="MODEL_NOT_LOADED", message="Model not loaded")

        query = params.get("query", "")
        passages = params.get("passages", [])
        if not query:
            raise EngineError(code="INVALID_PARAMS", message="query required")
        if not passages:
            raise EngineError(code="INVALID_PARAMS", message="passages required")

        logger.info("🔍 [worker] Reranking %d passages", len(passages))

        rerank_method = getattr(self.engine, "rerank", None)
        if not callable(rerank_method):
            raise EngineError(
                code="NOT_IMPLEMENTED",
                message="Model does not support reranking",
            )

        loop = asyncio.get_running_loop()
        scores = await loop.run_in_executor(
            None,
            lambda: rerank_method(query, passages),
        )

        logger.info("✅ [worker] Reranked %d passages", len(scores))
        return {
            "scores": scores,
            "model": params.get("model", ""),
        }
