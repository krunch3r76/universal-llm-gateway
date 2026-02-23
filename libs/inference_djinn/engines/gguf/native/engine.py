"""
Native llama.cpp engine implementation using subprocess wrapper.

Provides BaseEngine-compatible interface wrapping llama-server for:
- Parallel request processing (multiple concurrent requests on same model)
- Text embeddings (via --embedding mode with /v1/embeddings endpoint)
- Router mode integration (multi-model management)
- Production-ready server lifecycle management

ARCHITECTURE NOTES (embedding mode):
- When embedding=True: server starts with --embedding --pooling cls
- create_embedding() is SYNC (not async) — called via run_in_executor by RPC handler
- Task prefixes (Nomic) applied client-side — llama-server doesn't support them
- Flash attention auto-disabled for BERT/embedding architectures
"""

import asyncio
import hashlib
import os
from collections.abc import AsyncGenerator
from typing import Any, override

import httpx
from universal_logging import get_logger

from inference_djinn.engines.base import BaseEngine
from inference_djinn.http.openai_client import OpenAIServerClient
from inference_djinn.utils.types import TokenCountResult

from .config import APIFormat, ServerConfig
from .server import (
    LlamaServerManager,
    ServerStatus,
)
from .validation import (
    validate_response_format,
    verify_structured_output,
    verify_structured_output_content,
)

logger = get_logger(__name__)


class NativeGGUFEngine(BaseEngine):
    """
    GGUF inference engine using native llama-server subprocess.

    Spawns llama-server and communicates via HTTP. Supports parallel request
    processing, continuous batching, router mode (multi-model), and embedding mode.
    """

    def __init__(
        self,
        model_path: str | None = None,
        *,
        # Router mode
        models_dir: str | None = None,
        models_max: int = 4,
        # Unix socket configuration (preferred — eliminates port conflicts)
        socket_dir: str = "/tmp/llama-server",
        use_unix_socket: bool = True,
        # Server configuration (TCP fallback)
        host: str = "127.0.0.1",
        port: int = 8080,
        # Parallel processing
        # parallel_slots defaults from gateway's max_concurrent_per_worker (injected)
        # If not injected, defaults to 1 (single request processing)
        parallel_slots: int = 1,
        continuous_batching: bool = True,
        # Context configuration
        ctx_size: int = 8192,
        n_gpu_layers: int = -1,
        # CPU threading (¬set → falls back to LLAMA_ARG_THREADS env var)
        n_threads: int | None = None,
        n_threads_batch: int | None = None,
        # API format
        api_format: APIFormat = APIFormat.OPENAI,
        # Advanced options
        flash_attn: bool = True,
        no_mmap: bool = False,
        mlock: bool = True,
        numa: bool = False,
        # Batch and KV cache (defaults in ServerConfig)
        batch_size: int | None = None,
        cache_type_k: str | None = None,
        cache_type_v: str | None = None,
        # Vision models
        mmproj_path: str | None = None,
        # Embedding mode
        # TRICKY: When embedding=True (from loader_config):
        # 1. ServerConfig gets embedding=True → llama-server starts with --embedding
        # 2. flash_attn auto-disabled (BERT architectures don't support it)
        # 3. create_embedding() method becomes available
        # 4. generate() / generate_stream() will NOT work (server is embedding-only)
        embedding: bool = False,
        pooling: str | None = None,
        ubatch_size: int | None = None,
        embedding_task_default: str | None = None,
        embedding_task_prefixes: dict[str, str] | None = None,
        # Logging
        verbose: bool = False,
        # Timeouts
        timeout: int = 600,
        startup_timeout: float = 60.0,
        **kwargs: Any,
    ):
        """Initialize native GGUF engine. See parameter defaults for config options."""
        # Auto-detect mmproj from catalog config or co-located file
        if mmproj_path is None and model_path:
            # Check if catalog provided clip_model_path
            mmproj_path = kwargs.get("clip_model_path")

            # Check for model-specific mmproj file (matching model filename)
            # ¬auto-detect unrelated mmproj files (causes crashes for non-vision models)
            if mmproj_path is None:
                from pathlib import Path

                model_path_obj = Path(model_path)
                model_dir = model_path_obj.parent
                model_stem = model_path_obj.stem  # e.g., "qwen2-vl-7b"

                # Look for mmproj with matching model name
                mmproj_pattern = f"{model_stem}*mmproj*.gguf"
                mmproj_candidates = list(model_dir.glob(mmproj_pattern))
                if mmproj_candidates:
                    mmproj_path = str(mmproj_candidates[0])
                    logger.info(f"Auto-detected mmproj: {mmproj_path}")

        # Auto-disable flash_attn for embedding models (BERT doesn't support it)
        if embedding:
            flash_attn = False

        # COMPAT: Accept n_ctx as alias for ctx_size (catalog config uses n_ctx)
        # Explicit ctx_size takes precedence over n_ctx from kwargs
        if "n_ctx" in kwargs and ctx_size == 8192:  # 8192 is the default
            n_ctx_value = kwargs.get("n_ctx")
            if isinstance(n_ctx_value, int) and n_ctx_value > 0:
                ctx_size = n_ctx_value
                logger.info(
                    f"[NativeGGUFEngine] Using n_ctx={n_ctx_value} as ctx_size "
                    "(catalog compat)"
                )

        # COMPAT: Accept YAML loader config key names (translate to engine params)
        if "use_mmap" in kwargs:
            no_mmap = not kwargs.pop("use_mmap")
        if "use_mlock" in kwargs:
            mlock = kwargs.pop("use_mlock")
        if "f16_kv" in kwargs:
            f16_kv_val: bool = kwargs.pop("f16_kv")
            cache_type_k = "f16" if f16_kv_val else "f32"
            cache_type_v = "f16" if f16_kv_val else "f32"
        if "n_batch" in kwargs:
            batch_size = kwargs.pop("n_batch")

        # Extract KV cache clearing config from warmup settings.
        # Invariant: ∀ warmup config absent ⟹ default to False (rely on llama-server default behavior)
        warmup_dict: dict[str, Any] = kwargs.pop("warmup", {})
        self._clear_kv_streaming: bool = warmup_dict.get("streaming", {}).get(
            "clear_kv_before", False
        )
        self._clear_kv_non_streaming: bool = warmup_dict.get("non_streaming", {}).get(
            "clear_kv_before", False
        )
        if self._clear_kv_streaming or self._clear_kv_non_streaming:
            logger.info(
                f"[NativeGGUFEngine] KV cache clear enabled — "
                f"streaming={self._clear_kv_streaming}, "
                f"non_streaming={self._clear_kv_non_streaming}"
            )

        # DEBUG: Log parallel_slots value received
        logger.info(
            f"[NativeGGUFEngine] Initializing with parallel_slots={parallel_slots}, "
            f"ctx_size={ctx_size} for model: {model_path or models_dir}"
        )

        # Generate unique Unix socket path from model_path to avoid port conflicts
        socket_path: str | None = None
        if use_unix_socket and model_path:
            model_hash = hashlib.sha256(model_path.encode()).hexdigest()[:12]
            socket_path = f"{socket_dir}/{model_hash}.sock"
            os.makedirs(socket_dir, exist_ok=True)

        # Only forward batch/cache fields when explicitly provided (ServerConfig owns defaults)
        optional_fields: dict[str, int | str] = {}
        if batch_size is not None:
            optional_fields["batch_size"] = batch_size
        if cache_type_k is not None:
            optional_fields["cache_type_k"] = cache_type_k
        if cache_type_v is not None:
            optional_fields["cache_type_v"] = cache_type_v

        self.config = ServerConfig(
            model_path=model_path,
            models_dir=models_dir,
            models_max=models_max,
            host=host,
            port=port,
            socket_path=socket_path,
            parallel_slots=parallel_slots,
            continuous_batching=continuous_batching,
            ctx_size=ctx_size,
            n_gpu_layers=n_gpu_layers,
            n_threads=n_threads,
            n_threads_batch=n_threads_batch,
            api_format=api_format,
            flash_attn=flash_attn,
            no_mmap=no_mmap,
            mlock=mlock,
            numa=numa,
            mmproj_path=mmproj_path,
            embedding=embedding,
            pooling=pooling or ("cls" if embedding else None),
            ubatch_size=ubatch_size or (8192 if embedding else None),
            verbose=verbose,
            timeout=timeout,
            **optional_fields,
        )
        self.startup_timeout = startup_timeout

        self.server_manager: LlamaServerManager | None = None
        self.client: OpenAIServerClient | None = None
        self._crashed = False

        # Embedding task prefix configuration (for models like Nomic)
        # TRICKY: llama-server doesn't support task prefixes natively.
        # Must be applied client-side before calling /v1/embeddings.
        self._embedding_mode = embedding
        self._embedding_task_default = embedding_task_default
        self._embedding_task_prefixes = embedding_task_prefixes

        # Set engine type to distinguish from llama-cpp-python GGUF
        # Native server supports parallel requests via slots
        self.engine_type = "gguf_native"

    @override
    async def load(self) -> None:
        """
        Start llama-server and wait for it to become healthy.

        Raises:
            RuntimeError: If server fails to start
            TimeoutError: If server doesn't become healthy
        """
        logger.info("🚀 [NativeGGUFEngine] Loading engine...")

        self._crashed = False
        # Create server manager
        self.server_manager = LlamaServerManager(self.config)

        try:
            # Start server
            await self.server_manager.start(startup_timeout=self.startup_timeout)

            # Create client — pass socket_path for UDS transport
            self.client = OpenAIServerClient(
                base_url=self.server_manager.base_url,
                timeout=self.config.timeout,
                socket_path=self.config.socket_path,
            )

            logger.info("✅ [NativeGGUFEngine] Engine loaded successfully")
        except Exception as e:
            logger.error(
                f"❌ [NativeGGUFEngine] Engine load failed: {e!r}",
                exc_info=True,
            )
            # Fallback: cleanup server to prevent resource leaks
            if self.server_manager:
                logger.warning(
                    "[NativeGGUFEngine] Performing cleanup after load failure "
                    "(fallback to prevent dangling server process)"
                )
                try:
                    await self.server_manager.stop()
                except Exception as cleanup_error:
                    logger.error(
                        f"[NativeGGUFEngine] Cleanup failed: {cleanup_error!r}",
                        exc_info=True,
                    )
                finally:
                    self.server_manager = None
            raise

    @override
    async def unload(self) -> None:
        """Stop llama-server gracefully."""
        logger.info("🛑 [NativeGGUFEngine] Unloading engine...")

        if self.client:
            await self.client.__aexit__(None, None, None)
            self.client = None

        if self.server_manager:
            await self.server_manager.stop()
            self.server_manager = None

        logger.info("✅ [NativeGGUFEngine] Engine unloaded")

    @override
    async def generate(
        self,
        data: dict[str, Any],
        cancellation_event: asyncio.Event | None = None,
    ) -> dict[str, Any]:
        """Non-streaming generation via llama-server HTTP API.

        Args:
            data: Generation parameters with prompt/messages
            cancellation_event: Optional cancellation signal

        Returns:
            OpenAI-compatible completion response
        """
        if not self.client:
            raise RuntimeError("Engine not loaded — call load() first")

        messages = data.get("messages")
        prompt = data.get("prompt")

        # Build params (exclude metadata and routing fields)
        params = self._get_generation_params(data)
        if self._clear_kv_non_streaming:
            params["cache_prompt"] = False
        params["stream"] = False

        # Validate response_format schema if present
        response_format = params.get("response_format")
        if response_format:
            validate_response_format(response_format)

        if messages:
            coro = self.client.chat_completions(
                messages=messages,
                **params,
            )
        elif prompt:
            coro = self.client.completions(
                prompt=prompt,
                **params,
            )
        else:
            raise ValueError("data must contain 'messages' or 'prompt'")

        # Cancellation: poll event while waiting so connection closes → slot freed
        if cancellation_event is None:
            result = await coro
        else:
            task = asyncio.create_task(coro)
            try:
                while not task.done():
                    if cancellation_event.is_set():
                        task.cancel()
                        try:
                            await task
                        except asyncio.CancelledError:
                            pass
                        raise asyncio.CancelledError
                    await asyncio.sleep(0.1)
                result = task.result()
            except asyncio.CancelledError:
                if not task.done():
                    task.cancel()
                raise

        # Post-validation: verify output matches schema
        if response_format and response_format.get("type") == "json_schema":
            verify_structured_output(result, response_format)

        return result

    @override
    async def generate_stream(
        self,
        data: dict[str, Any],
        cancellation_event: asyncio.Event | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Streaming generation via llama-server SSE.

        Args:
            data: Generation parameters with prompt/messages
            cancellation_event: Optional cancellation signal

        Yields:
            OpenAI-compatible streaming chunks
        """
        if not self.client:
            raise RuntimeError("Engine not loaded — call load() first")

        messages = data.get("messages")
        prompt = data.get("prompt")

        params = self._get_generation_params(data)
        if self._clear_kv_streaming:
            params["cache_prompt"] = False
        params["stream"] = True

        # Validate response_format schema if present
        response_format = params.get("response_format")
        if response_format:
            validate_response_format(response_format)

        # Track content for post-validation
        collected_content: list[str] = []

        if messages:
            stream = self.client.chat_completions_stream(
                messages=messages,
                **params,
            )
        elif prompt:
            stream = self.client.completions_stream(
                prompt=prompt,
                **params,
            )
        else:
            raise ValueError("data must contain 'messages' or 'prompt'")

        async for chunk in stream:
            if cancellation_event and cancellation_event.is_set():
                await stream.aclose()
                yield {"choices": [{"delta": {}, "finish_reason": "cancelled"}]}
                return

            # Collect content for post-validation
            if response_format and response_format.get("type") == "json_schema":
                first_choice = chunk.get("choices", [{}])[0]
                delta = first_choice.get("delta", {})
                piece: str | None = None
                if isinstance(delta, dict):
                    delta_content = delta.get("content")
                    if isinstance(delta_content, str) and delta_content:
                        piece = delta_content

                if piece is None:
                    text = first_choice.get("text")
                    if isinstance(text, str) and text:
                        piece = text

                if piece is not None:
                    collected_content.append(piece)

            yield chunk

        # Post-validation for structured output
        if response_format and response_format.get("type") == "json_schema":
            if collected_content:
                full_content = "".join(collected_content)
                verify_structured_output_content(full_content)
            else:
                logger.warning(
                    "Structured output stream validation skipped: no content collected from chunks (expected choices[0].delta.content or choices[0].text)"
                )

    @override
    async def count_tokens_for_messages(
        self,
        messages_or_prompt: list[dict[str, Any]] | str,
        use_cpu: bool = True,
        context_length: int | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> TokenCountResult:
        """Count tokens using llama-server /tokenize endpoint."""
        if not self.client:
            return TokenCountResult(
                tokens=0,
                method="error",
                success=False,
                error="Engine not loaded",
            )

        if self._crashed:
            return TokenCountResult(
                tokens=0,
                method="error",
                success=False,
                error="Engine process crashed",
            )

        try:
            if isinstance(messages_or_prompt, str):
                text = messages_or_prompt
            else:
                text = " ".join(
                    msg.get("content", "")
                    for msg in messages_or_prompt
                    if isinstance(msg.get("content"), str)
                )

            tokens = await self.client.tokenize(text)
            return TokenCountResult(
                tokens=len(tokens),
                method="native_tokenizer",
                success=True,
            )
        except (ConnectionRefusedError, httpx.ConnectError) as e:
            logger.error(
                f"❌ [NativeGGUFEngine] Server unreachable during token count: {e}"
            )
            self._crashed = True
            return TokenCountResult(
                tokens=0,
                method="error",
                success=False,
                error=f"Engine process unreachable: {e}",
            )
        except Exception as e:
            logger.warning(f"Token counting failed, using approximation: {e}")
            approx = len(str(messages_or_prompt)) // 4
            return TokenCountResult(
                tokens=approx,
                method="approximation",
                success=False,
                error=str(e),
            )

    @override
    def get_model_info(self) -> dict[str, Any]:
        """Return model information."""
        return {
            "engine_type": "native-llama-server",
            "model_path": self.config.model_path,
            "parallel_slots": self.config.parallel_slots,
            "ctx_size": self.config.ctx_size,
            "n_gpu_layers": self.config.n_gpu_layers,
            "continuous_batching": self.config.continuous_batching,
            "flash_attn": self.config.flash_attn,
            "router_mode": self.config.models_dir is not None,
            "api_format": self.config.api_format.value,
            "vision_enabled": self.config.mmproj_path is not None,
            "mmproj_path": self.config.mmproj_path,
            "embedding_mode": self._embedding_mode,
            "pooling": self.config.pooling,
        }

    def is_loaded(self) -> bool:
        """Check if engine is loaded and healthy."""
        if self._crashed:
            return False
        if not self.server_manager:
            return False
        return self.server_manager.status == ServerStatus.RUNNING

    # Router mode operations

    async def list_models(self) -> dict[str, Any]:
        """List available models (router mode only)."""
        if not self.config.models_dir:
            raise RuntimeError("list_models only available in router mode")
        if not self.client:
            raise RuntimeError("Engine not loaded")
        return await self.client.list_models()

    async def load_model(self, model: str) -> dict[str, Any]:
        """Manually load model (router mode only)."""
        if not self.config.models_dir:
            raise RuntimeError("load_model only available in router mode")
        if not self.client:
            raise RuntimeError("Engine not loaded")
        return await self.client.load_model(model)

    async def unload_model(self, model: str) -> dict[str, Any]:
        """Manually unload model (router mode only)."""
        if not self.config.models_dir:
            raise RuntimeError("unload_model only available in router mode")
        if not self.client:
            raise RuntimeError("Engine not loaded")
        return await self.client.unload_model(model)

    async def health(self) -> dict[str, Any]:
        """Check server health."""
        if not self.client:
            raise RuntimeError("Engine not loaded")
        return await self.client.health()

    # Embedding operations

    def create_embedding(
        self,
        input_texts: list[str],
        task: str | None = None,
    ) -> dict[str, Any]:
        """Generate embeddings via llama-server /v1/embeddings (sync).

        Sync because RPC handler calls via run_in_executor().
        No retry — readiness probe in LlamaServerManager.start() guarantees
        the server accepts inference requests before MODEL_LOADED is emitted.
        """
        if not self._embedding_mode:
            raise RuntimeError(
                "Engine not in embedding mode. Set embedding=True in loader config."
            )
        if not self.server_manager:
            raise RuntimeError("Engine not loaded — call load() first")

        texts = self._apply_task_prefix(input_texts, task)

        logger.debug(
            f"[embedding] endpoint={self.server_manager.base_url}/v1/embeddings, "
            f"server_status={self.server_manager.status.value}, "
            f"embedding_mode={self._embedding_mode}, "
            f"input_count={len(texts)}"
        )

        try:
            if self.config.socket_path:
                transport = httpx.HTTPTransport(uds=self.config.socket_path)
                with httpx.Client(
                    transport=transport, base_url="http://localhost"
                ) as client:
                    response = client.post(
                        "/v1/embeddings",
                        json={"input": texts},
                        timeout=float(self.config.timeout),
                    )
            else:
                response = httpx.post(
                    f"http://{self.config.host}:{self.config.port}/v1/embeddings",
                    json={"input": texts},
                    timeout=float(self.config.timeout),
                )

            logger.debug(
                f"[embedding] status={response.status_code}, "
                f"content_type={response.headers.get('content-type')}"
            )

            if response.status_code != 200:
                logger.error(
                    f"[embedding] Non-200 response: status={response.status_code}, "
                    f"body={response.text[:500]}"
                )

            response.raise_for_status()
            return response.json()

        except httpx.HTTPStatusError as e:
            logger.error(
                f"[embedding] HTTP error: status={e.response.status_code}, "
                f"url={e.request.url}, body={e.response.text[:500]}"
            )
            raise

    def _apply_task_prefix(
        self,
        input_texts: list[str],
        task: str | None,
    ) -> list[str]:
        """Apply task prefix to input texts if configured.

        TRICKY: Nomic embedding models require task-specific prefixes:
        - "search_document: " for indexing documents
        - "search_query: " for queries
        - "clustering: " for clustering tasks
        llama-server doesn't handle this — we prepend client-side.

        Args:
            input_texts: Raw input texts
            task: Explicit task override, or None for default

        Returns:
            Texts with prefix applied (or unchanged if no prefix configured)
        """
        effective_task = task or self._embedding_task_default
        if effective_task and self._embedding_task_prefixes:
            prefix = self._embedding_task_prefixes.get(effective_task)
            if prefix:
                return [f"{prefix}{text}" for text in input_texts]
        return input_texts

    async def __aenter__(self):
        """Async context manager entry."""
        await self.load()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.unload()
