"""RPC handlers for embedding generation."""

import asyncio
from typing import Any

from universal_logging import get_logger
from universal_protocol.errors import EngineError

logger = get_logger(__name__)


class EmbeddingHandlers:
    """RPC handlers for embedding generation (mixin for Worker class)."""

    async def handle_generate_embeddings(
        self, params: dict[str, Any]
    ) -> dict[str, Any]:
        """Handle generate_embeddings RPC for text embedding."""
        if not self.engine or not self.engine.is_loaded():
            raise EngineError(code="MODEL_NOT_LOADED", message="Model not loaded")

        input_texts = params.get("input", [])
        if not input_texts:
            raise EngineError(code="INVALID_PARAMS", message="input required")

        if isinstance(input_texts, str):
            input_texts = [input_texts]

        correlation_id = params.get("correlation_id")
        logger.info(f"📊 [worker] Generating embeddings for {len(input_texts)} texts")

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            lambda: self.engine.create_embedding(
                input_texts,
                correlation_id=correlation_id,
            ),
        )

        logger.info(f"✅ [worker] Generated {len(result.get('data', []))} embeddings")
        return result
