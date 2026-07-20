"""Multimodal inference (transcription, image, embeddings, rerank) mixin."""

from typing import Any

from ._runtime import _emit_embedding_debug, _get_resource_tracker, logger


class MultimodalMixin:
    """Transcription, image generation, embeddings, rerank, and timeout recycle."""

    async def transcribe_file(
        self,
        model_id: str,
        audio_file_path: str,
        language: str | None = None,
        prompt: str | None = None,
        temperature: float = 0.0,
        word_timestamps: bool = True,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """
        Transcribe an audio file using a Whisper model.

        Args:
            model_id: Whisper model ID (must be loaded)
            audio_file_path: Path to audio file on disk
            language: Optional language code (e.g., "en", "fa")
            prompt: Optional text to guide transcription style
            temperature: Sampling temperature (0.0 to 1.0)
            word_timestamps: Whether to include word-level timestamps
            timeout: Optional processing timeout in seconds

        Returns:
            Transcription result with text, language, duration, segments

        Raises:
            RuntimeError: If model not loaded or transcription fails
            TimeoutError: If timeout exceeded
        """
        params = {
            "audio_file_path": audio_file_path,
            "language": language,
            "prompt": prompt,
            "temperature": temperature,
            "word_timestamps": word_timestamps,
        }

        rpc_timeout = timeout if timeout is not None else 300.0
        return await self._call_rpc(model_id, "transcribe_file", params, rpc_timeout)

    async def generate_image(
        self,
        model_id: str,
        prompt: str,
        width: int = 1024,
        height: int = 1024,
        num_inference_steps: int = 20,  # FLUX.2 default
        guidance_scale: float = 4.0,  # FLUX.2 default
        seed: int | None = None,
        response_format: str = "b64_json",
        negative_prompt: str | None = None,
        caption_upsample_temperature: float | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """
        Generate an image using a Flux.2 model.

        Args:
            model_id: Flux.2 model ID (must be loaded)
            prompt: Text prompt for image generation
            width: Image width in pixels
            height: Image height in pixels
            num_inference_steps: Number of denoising steps
            guidance_scale: Guidance strength
            seed: Optional random seed for reproducibility
            response_format: Response format ("url" or "b64_json")
            negative_prompt: Optional negative prompt (what to avoid)
            caption_upsample_temperature: Optional caption upsampling temp
                (0.15 recommended for FLUX.2)
            timeout: Optional processing timeout in seconds

        Returns:
            Image generation result with created timestamp and data list

        Raises:
            RuntimeError: If model not loaded or generation fails
            TimeoutError: If timeout exceeded
        """
        params = {
            "prompt": prompt,
            "width": width,
            "height": height,
            "num_inference_steps": num_inference_steps,
            "guidance_scale": guidance_scale,
            "seed": seed,
            "response_format": response_format,
            "negative_prompt": negative_prompt,
            "caption_upsample_temperature": caption_upsample_temperature,
        }

        rpc_timeout = timeout if timeout is not None else 300.0

        # Wrap with inference tracking (emits INFERENCE_STARTED/COMPLETED events)
        resource_tracker = _get_resource_tracker()
        async with resource_tracker.track_inference(model_id):
            return await self._call_rpc(model_id, "generate_image", params, rpc_timeout)

    async def generate_embeddings(
        self,
        model_id: str,
        input_texts: list[str],
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Generate embeddings via worker RPC.

        Args:
            model_id: Model identifier
            input_texts: Texts to embed
            correlation_id: Request correlation ID

        Returns:
            OpenAI-compatible embedding response

        Raises:
            RuntimeError: If model not loaded, RPC fails, or timeout triggers quarantine
        """
        params = {
            "input": input_texts,
            "model": model_id,
            "correlation_id": correlation_id,
        }

        resource_tracker = _get_resource_tracker()
        try:
            async with resource_tracker.track_inference(model_id):
                result = await self._call_rpc(model_id, "generate_embeddings", params)
        except TimeoutError as exc:
            # track_inference already set model to IDLE — override to ERROR.
            # The worker thread is likely still stuck on the llama-server call;
            # force-recycle kills the worker (and its llama-server child) to
            # release GPU memory and prevent capacity from being overstated.
            resource_tracker.set_model_error(
                model_id, f"Embedding timeout — worker recycled: {exc}"
            )
            await self._recycle_after_embedding_timeout(model_id, correlation_id)
            raise RuntimeError(
                f"Embedding timed out — worker process terminated for {model_id}"
            ) from exc
        return result

    async def rerank(
        self,
        model_id: str,
        query: str,
        passages: list[str],
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Rerank passages via worker RPC.

        Args:
            model_id: Reranker model identifier
            query: Query to score against passages
            passages: Passages to score
            correlation_id: Request correlation ID

        Returns:
            Dict with "scores" (list[float]) and "model" (str)

        Raises:
            RuntimeError: If model not loaded or RPC fails
        """
        params = {
            "query": query,
            "passages": passages,
            "model": model_id,
        }

        resource_tracker = _get_resource_tracker()
        try:
            async with resource_tracker.track_inference(model_id):
                result = await self._call_rpc(model_id, "rerank", params)
        except TimeoutError as exc:
            resource_tracker.set_model_error(
                model_id, f"Rerank timeout — worker recycled: {exc}"
            )
            await self._recycle_after_rerank_timeout(model_id, correlation_id)
            raise RuntimeError(
                f"Rerank timed out — worker process terminated for {model_id}"
            ) from exc
        return result

    async def _recycle_after_rerank_timeout(
        self, model_id: str, correlation_id: str | None = None
    ) -> None:
        """Force-recycle worker after rerank timeout to release GPU resources."""
        logger.error(f"⏰ Rerank timeout for {model_id} — killing worker process")
        sup = self._process_state.get_supervisor(model_id)
        if sup:
            try:
                await sup.stop(force=True, timeout=5)
                logger.info(f"✅ Killed timed-out rerank worker for {model_id}")
            except Exception as e:
                logger.error(
                    f"Failed to kill timed-out rerank worker for {model_id}: {e}"
                )
        self._process_state.remove_supervisor(model_id)
        self._process_state.remove_socket_path(model_id)
        await self._cleanup_socket_file(model_id)

    async def _recycle_after_embedding_timeout(
        self, model_id: str, correlation_id: str | None = None
    ) -> None:
        """Force-recycle worker after embedding timeout to release GPU resources.

        Kills the worker process tree (including llama-server child), removes
        supervisor tracking, and emits a quarantine event. The model remains in
        ERROR state; auto-load-on-request will reload it when the next request
        arrives.
        """
        logger.error(f"⏰ Embedding timeout for {model_id} — killing worker process")
        await _emit_embedding_debug(
            "timeout_quarantine",
            model_id,
            correlation_id,
            action="force_recycle",
        )
        sup = self._process_state.get_supervisor(model_id)
        if sup:
            try:
                await sup.stop(force=True, timeout=5)
                logger.info(f"✅ Killed timed-out embedding worker for {model_id}")
            except Exception as e:
                logger.error(
                    f"Failed to kill timed-out embedding worker for {model_id}: {e}"
                )
        self._process_state.remove_supervisor(model_id)
        self._process_state.remove_socket_path(model_id)
        await self._cleanup_socket_file(model_id)
