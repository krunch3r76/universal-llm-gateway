"""
CrossEncoder engine — scores (query, passage) pairs via sentence_transformers.

Lifecycle:
    - __init__: stores config, no GPU allocation
    - load(): loads CrossEncoder model to device, sets self.loaded = True
    - rerank(query, passages): scores pairs, returns list[float]
    - unload(): deletes model, frees GPU memory

∀ non-rerank abstract methods from BaseEngine: raise NotImplementedError
(this engine does not generate text, stream, or count tokens).
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from universal_logging import get_logger

from inference_djinn.engines.base import BaseEngine

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

logger = get_logger(__name__)


class CrossEncoderEngine(BaseEngine):
    """Reranker engine backed by sentence_transformers CrossEncoder."""

    def __init__(
        self,
        model_path: str,
        *,
        device: str = "cuda",
        trust_remote_code: bool = True,
        max_length: int | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(model_path, **kwargs)
        self.engine_type = "cross-encoder"
        self._device = device
        self._trust_remote_code = trust_remote_code
        self._max_length = max_length
        self._model: Any = None

    async def load(self) -> None:
        """Load CrossEncoder model onto device."""
        from sentence_transformers import CrossEncoder

        logger.info(
            "Loading cross-encoder: %s (device=%s)", self.model_path, self._device
        )
        start = time.monotonic()

        init_kwargs: dict[str, Any] = {
            "device": self._device,
            "trust_remote_code": self._trust_remote_code,
        }
        if self._max_length is not None:
            init_kwargs["max_length"] = self._max_length

        self._model = CrossEncoder(self.model_path, **init_kwargs)
        self.loaded = True

        elapsed = time.monotonic() - start
        logger.info("Cross-encoder loaded in %.2fs: %s", elapsed, self.model_path)

    def rerank(self, query: str, passages: list[str]) -> list[float]:
        """Score (query, passage) pairs. Returns one float per passage (same order).

        Sync — caller must use run_in_executor for async contexts.
        """
        if not self._model or not self.loaded:
            raise RuntimeError("Cross-encoder model not loaded")
        if not passages:
            return []
        pairs = [(query, p) for p in passages]
        scores = self._model.predict(pairs)
        return [float(s) for s in scores]

    async def unload(self) -> None:
        """Free model and GPU memory."""
        if self._model is not None:
            del self._model
            self._model = None
        self.loaded = False

        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass

        logger.info("Cross-encoder unloaded: %s", self.model_path)

    def get_model_info(self) -> dict[str, Any]:
        """Return runtime metadata used by engine registries and diagnostics."""
        return {
            "model_path": self.model_path,
            "engine_type": self.engine_type,
            "loaded": self.loaded,
            "device": self._device,
        }

    async def generate(
        self, data: dict[str, Any], cancellation_event: Any = None
    ) -> dict[str, Any]:
        raise NotImplementedError("CrossEncoderEngine does not support text generation")

    async def generate_stream(
        self, data: dict[str, Any], cancellation_event: Any = None
    ) -> AsyncGenerator[dict[str, Any], None]:
        raise NotImplementedError("CrossEncoderEngine does not support streaming")
        yield  # unreachable — makes this a generator

    async def count_tokens_for_messages(
        self,
        messages_or_prompt: list[dict[str, Any]] | str,
        use_cpu: bool = True,
        context_length: int | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> Any:
        raise NotImplementedError("CrossEncoderEngine does not support token counting")
