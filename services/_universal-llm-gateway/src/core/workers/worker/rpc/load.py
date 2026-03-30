"""Model lifecycle RPC handlers (load/unload).

Single-flight guard: _load_lock prevents concurrent handle_load_model RPCs
from double-initializing the engine within the same worker process.
"""

import asyncio

from universal_concurrency import FifoCapacityGate
from universal_logging import get_logger
from universal_protocol.errors import EngineError

logger = get_logger(__name__)


class LoadHandlers:
    """Mix-in class for model lifecycle RPC handlers."""

    # Assumes self.model_id, self.model_config, self.model_loaded
    # Assumes self.engine, self._load_model_engine() exist

    # Inference gate (initialized after engine load)
    _inference_gate: FifoCapacityGate | None = None
    _load_lock: asyncio.Lock | None = None

    def _get_load_lock(self) -> asyncio.Lock:
        if self._load_lock is None:
            self._load_lock = asyncio.Lock()
        return self._load_lock

    def _init_inference_gate(self, request_id: str) -> None:
        """
        Initialize inference capacity gate after engine is loaded.

        Limit from streaming_config.parallel_slots (default 1 = serial).
        Models with parallel_slots > 1 enable batched concurrent inference.

        Args:
            request_id: For logging
        """
        model_config = self.model_config or {}
        streaming_config = model_config.get("streaming_config", {})
        parallel_slots = streaming_config.get("parallel_slots", 1)
        self._inference_gate = FifoCapacityGate(
            limit=parallel_slots,
            gate_id=f"worker:{self.model_id}",
        )
        logger.info(
            f"✅ [worker] [request_id={request_id}] "
            f"Inference gate initialized (parallel_slots={parallel_slots})"
        )

    def _validate_load_request_params(
        self,
        rpc_name: str | None,
        rpc_path: str | None,
        request_id: str,
    ) -> None:
        """
        Validate load request parameters against already-loaded model.

        Pre: self.engine is not None ∧ self.engine.is_loaded() ∧ (rpc_name ∨ rpc_path)
        Post: (loaded_model ≡ requested_model) ∨ EngineError raised

        Args:
            rpc_name: Requested model name (optional)
            rpc_path: Requested model path (optional)
            request_id: RPC request ID for logging

        Raises:
            EngineError: If requested model differs from loaded model
        """
        if not (rpc_name or rpc_path):
            return

        loaded_name = self.model_config.get("name") if self.model_config else None
        loaded_path = self.model_config.get("path") if self.model_config else None

        if (rpc_name and loaded_name and rpc_name != loaded_name) or (
            rpc_path and loaded_path and rpc_path != loaded_path
        ):
            raise EngineError(
                code="MODEL_MISMATCH",
                message=f"Model already loaded: {loaded_name or loaded_path}. "
                f"Cannot load different model: {rpc_name or rpc_path}",
                data={
                    "loaded_model": loaded_name or loaded_path,
                    "requested_model": rpc_name or rpc_path,
                },
            )

    def _extract_context_size_from_catalog(
        self,
        request_id: str,
    ) -> int | None:
        """
        Extract context_size from catalog metadata (single source of truth).

        Invariant: ∀ LLM model ∈ catalog: ∃! (max_model_len ∨ n_ctx)

        Note: Returns None for non-LLM models (e.g., Whisper) that
        don't have context size.

        Args:
            request_id: RPC request ID for logging

        Returns:
            Context size from catalog metadata, or None for non-LLM models

        Raises:
            EngineError: If max_model_len/n_ctx missing from catalog for LLM models
        """
        # Check if this is a non-LLM model (e.g., Whisper, Flux)
        engine_type = self.model_config.get("engine") if self.model_config else None
        if engine_type in ("faster-whisper", "diffusers"):
            logger.debug(
                f"🔧 [worker] [request_id={request_id}] "
                f"Model {self.model_id} is engine={engine_type}, "
                "skipping context_size extraction"
            )
            return None

        context_size = None
        if self.model_config:
            loader_config = self.model_config.get("loader_config") or {}
            context_size = loader_config.get("max_model_len")
            if context_size is None:
                # Fallback to n_ctx for GGUF models
                context_size = loader_config.get("n_ctx")

        # Only require context_size for confirmed LLM models
        # Unknown engine (None) should not fail - log warning instead
        if context_size is None:
            if engine_type is None:
                logger.warning(
                    f"⚠️ [worker] [request_id={request_id}] "
                    f"Model engine unknown for {self.model_id}, "
                    "cannot verify context_size requirement"
                )
                return None
            else:
                logger.error(
                    f"❌ [worker] [request_id={request_id}] "
                    f"No max_model_len in catalog metadata for {self.model_id}"
                )
                raise EngineError(
                    code="CONFIG_ERROR",
                    message=(
                        f"Missing max_model_len in catalog metadata for {self.model_id}"
                    ),
                )

        return context_size

    async def handle_load_model(self, params: dict) -> dict:
        """Handle load_model RPC request with single-flight lock.

        Concurrent RPCs for the same worker serialize through _load_lock
        so only one engine initialization runs at a time.
        """
        request_id = params.get("_request_id", "unknown")
        logger.info(
            f"🔧 [worker] [request_id={request_id}] "
            f"Load model RPC received for {self.model_id}"
        )

        async with self._get_load_lock():
            rpc_name = params.get("name")
            rpc_path = params.get("path")

            # Double-check after acquiring lock — another RPC may have loaded it
            if self.engine and self.engine.is_loaded():
                self._validate_load_request_params(rpc_name, rpc_path, request_id)

                logger.info(
                    f"✅ [worker] [request_id={request_id}] "
                    f"Model {self.model_id} already loaded"
                )

                context_size = self._extract_context_size_from_catalog(request_id)
                engine_pid = self.engine.get_engine_pid()
                result = {
                    "success": True,
                    "model_loaded": True,
                    **(
                        {"context_size": context_size}
                        if context_size is not None
                        else {}
                    ),
                    **({"engine_pid": engine_pid} if engine_pid is not None else {}),
                }
                return result

            try:
                if not self.model_config:
                    self.model_config = {
                        "format": params.get("format", "vllm"),
                        "name": rpc_name,
                        "path": rpc_path or params.get("model_path", ""),
                        "loader_config": params.get("loader_config", {}),
                    }

                import time as _time

                from ..events import (
                    emit_model_failed,
                    emit_model_loaded,
                    emit_model_loading,
                )

                logger.info(
                    f"🔧 [worker] [request_id={request_id}] "
                    f"Loading model engine for {self.model_id}"
                )
                await emit_model_loading(
                    worker_id=self.worker_id, model_id=self.model_id
                )
                _t0 = _time.monotonic()
                await self._load_model_engine()

                await emit_model_loaded(
                    worker_id=self.worker_id,
                    model_id=self.model_id,
                    duration_s=_time.monotonic() - _t0,
                )

                self._init_inference_gate(request_id)

                context_size = self._extract_context_size_from_catalog(request_id)
                engine_pid = self.engine.get_engine_pid() if self.engine else None
                result = {
                    "success": True,
                    "model_loaded": True,
                    **(
                        {"context_size": context_size}
                        if context_size is not None
                        else {}
                    ),
                    **({"engine_pid": engine_pid} if engine_pid is not None else {}),
                }
                return result

            except Exception as e:
                await emit_model_failed(
                    worker_id=self.worker_id,
                    model_id=self.model_id,
                    error=str(e),
                )
                logger.error(
                    f"❌ [worker] [request_id={request_id}] Model loading error: {e}"
                )
                raise self._map_exception_to_engine_error(e)

    def _validate_unload_model_request(self, params: dict) -> None:
        """
        Validate unload_model RPC request parameters.

        Args:
            params: RPC parameters

        Raises:
            EngineError: If model_name is missing or doesn't match
        """
        model_name = params.get("name")
        if model_name and model_name != self.model_id:
            raise EngineError(
                code="INVALID_PARAMS",
                message=(
                    f"Cannot unload model {model_name}, worker handles {self.model_id}"
                ),
            )

    async def _notify_streams_of_unload(self) -> None:
        """Notify all active streams of model unload."""
        from universal_protocol.ws.registry import stream_registry

        notified = stream_registry.cancel_all_for_unload(self.model_id)
        if notified > 0:
            logger.info(f"📤 [worker] Notified {notified} streams of unload")

    async def _release_engine_resources(self) -> None:
        """Release engine resources (call unload method).

        Side-effects:
            engine.unload() called, self.engine set to None
        """
        if not self.engine:
            return

        logger.info(f"🔧 [worker] Releasing engine resources for {self.model_id}")
        try:
            if hasattr(self.engine, "unload"):
                await self.engine.unload()
                logger.info("✅ [worker] Engine unload method called successfully")
            else:
                logger.warning("⚠️ [worker] Engine has no unload method")
        except Exception as e:
            logger.error(f"❌ [worker] Error during engine unload: {e}")

        self.engine = None

    def _reset_model_state(self) -> None:
        """Reset model state flags after unload.

        Side-effects:
            model_loaded, model_config, _status["model_loaded"], _inference_gate reset
        """
        self.model_loaded = False
        self.model_config = None
        self._status["model_loaded"] = False
        self._inference_gate = None

    async def handle_unload_model(self, params: dict) -> dict:
        """
        Handle unload_model RPC request.

        Orchestrates: validation → notify → cleanup → release → reset

        Args:
            params: RPC parameters including model name

        Returns:
            Success response confirming model unloaded
        """
        logger.info("🔧 [worker] Unload model RPC received")

        from ..stream_lifecycle import cleanup_all_streams

        try:
            # 1. Validate
            self._validate_unload_model_request(params)

            # 2. Notify streams (best effort, before cleanup)
            await self._notify_streams_of_unload()

            # 3. Cleanup all streams (centralized)
            cleanup_count = await cleanup_all_streams(reason="unload")
            if cleanup_count > 0:
                logger.info(f"✅ [worker] Cleaned up {cleanup_count} active streams")

            # 4. Release engine
            await self._release_engine_resources()

            # 5. Reset state
            self._reset_model_state()

            logger.info(f"✅ [worker] Model {self.model_id} unloaded successfully")
            return {"success": True}

        except EngineError:
            raise
        except Exception as e:
            logger.error(f"❌ [worker] Error during model unload: {e}")
            raise self._map_exception_to_engine_error(e)
