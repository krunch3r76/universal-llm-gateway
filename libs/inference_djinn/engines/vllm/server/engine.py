"""
vLLM server engine (subprocess + OpenAI-compatible API).

Wraps vllm serve with HTTP client for tool-calling support and streaming.
"""

import hashlib
import os
from collections.abc import AsyncGenerator
from typing import Any, override

from universal_logging import get_logger

from inference_djinn.engines.base import BaseEngine
from inference_djinn.http.openai_client import OpenAIServerClient
from inference_djinn.utils.types import TokenCountResult

from .config import VLLMServerConfig, detect_tool_call_parser
from .manager import ServerStatus, VLLMServerManager

logger = get_logger(__name__)


class VLLMServerEngine(BaseEngine):
    """
    vLLM inference engine using vllm serve subprocess.

    Spawns vllm serve and communicates via OpenAI-compatible HTTP API.
    Supports tool calling and streaming; replaces the library-based VLLMEngine.
    """

    def __init__(
        self,
        model_path: str,
        *,
        socket_dir: str = "/tmp/vllm-server",
        use_unix_socket: bool = True,
        host: str = "127.0.0.1",
        port: int = 8000,
        tool_call_parser: str | None = None,
        enable_auto_tool_choice: bool = True,
        max_model_len: int | None = None,
        gpu_memory_utilization: float = 0.90,
        quantization: str | None = None,
        dtype: str = "auto",
        tensor_parallel_size: int = 1,
        startup_timeout: float = 600.0,
        timeout: float = 600.0,
        **kwargs: Any,
    ) -> None:
        super().__init__(model_path)
        self.engine_type = "vllm"

        socket_path: str | None = None
        if use_unix_socket and model_path:
            model_hash = hashlib.sha256(model_path.encode()).hexdigest()[:12]
            socket_path = f"{socket_dir}/{model_hash}.sock"
            os.makedirs(socket_dir, exist_ok=True)

        parser = tool_call_parser or detect_tool_call_parser(model_path)

        # Gateway-internal keys that are not vLLM CLI flags.
        # Strip here so extra_cli_args contains only vLLM-addressable fields.
        # ∀ key ∈ _internal_keys: not forwarded to vllm serve subprocess.
        self._event_bus = kwargs.pop("event_bus", None)

        _internal_keys = frozenset(
            {
                "warmup",
                "trust_remote_code",
                "embedding",
                "embedding_task_default",
                "embedding_task_prefixes",
                "n_ctx",
            }
        )
        is_embedding = kwargs.get("embedding") is True
        dropped = {k: v for k, v in kwargs.items() if k in _internal_keys}
        extra_cli_args = {k: v for k, v in kwargs.items() if k not in _internal_keys}
        if is_embedding:
            extra_cli_args["runner"] = "pooling"
            enable_auto_tool_choice = False
        if dropped:
            logger.debug(
                "🔧 [VLLMServerEngine] Stripped gateway-internal keys: %s",
                list(dropped),
            )
        if extra_cli_args:
            logger.info(
                "🔧 [VLLMServerEngine] Extra CLI args from catalog: %s",
                extra_cli_args,
            )

        self.config = VLLMServerConfig(
            model_path=model_path,
            host=host,
            port=port,
            socket_path=socket_path,
            enable_auto_tool_choice=enable_auto_tool_choice,
            tool_call_parser=parser,
            max_model_len=max_model_len,
            gpu_memory_utilization=gpu_memory_utilization,
            quantization=quantization,
            dtype=dtype,
            tensor_parallel_size=tensor_parallel_size,
            extra_cli_args=extra_cli_args,
        )
        self.startup_timeout = startup_timeout
        self._timeout = timeout
        self.server_manager: VLLMServerManager | None = None
        self.client: OpenAIServerClient | None = None
        self._crashed = False

    @override
    async def load(self) -> None:
        """Start vllm serve and wait for health."""
        logger.info("🚀 [VLLMServerEngine] Loading engine...")
        self._crashed = False
        self.server_manager = VLLMServerManager(self.config)
        try:
            await self.server_manager.start(startup_timeout=self.startup_timeout)
            self.client = OpenAIServerClient(
                base_url=self.server_manager.base_url,
                timeout=self._timeout,
                socket_path=self.config.socket_path,
                event_bus=self._event_bus,
            )
            self.loaded = True
            logger.info("✅ [VLLMServerEngine] Engine loaded successfully")
        except Exception as e:
            logger.error(
                f"❌ [VLLMServerEngine] Engine load failed: {e!r}",
                exc_info=True,
            )
            if self.server_manager:
                try:
                    await self.server_manager.stop()
                except Exception as cleanup_error:
                    logger.error(f"Cleanup failed: {cleanup_error!r}")
                finally:
                    self.server_manager = None
            raise

    @override
    async def unload(self) -> None:
        """Stop vllm serve and close client."""
        logger.info("🛑 [VLLMServerEngine] Unloading engine...")
        self.loaded = False
        if self.client:
            await self.client.__aexit__(None, None, None)
            self.client = None
        if self.server_manager:
            await self.server_manager.stop()
            self.server_manager = None
        logger.info("✅ [VLLMServerEngine] Engine unloaded")

    @override
    async def generate(
        self,
        data: dict[str, Any],
        cancellation_event: Any = None,
    ) -> dict[str, Any]:
        """Non-streaming generation via vllm serve HTTP API."""
        if not self.client:
            raise RuntimeError("Engine not loaded — call load() first")

        messages = data.get("messages")
        prompt = data.get("prompt")
        params = self._get_generation_params(data)
        params["stream"] = False

        if messages:
            result = await self.client.chat_completions(
                messages=messages,
                model=self.config.model_path,
                **params,
            )
        elif prompt:
            result = await self.client.completions(
                prompt=prompt,
                model=self.config.model_path,
                **params,
            )
        else:
            raise ValueError("data must contain 'messages' or 'prompt'")
        return result

    @override
    async def generate_stream(
        self,
        data: dict[str, Any],
        cancellation_event: Any = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Streaming generation via vllm serve SSE."""
        if not self.client:
            raise RuntimeError("Engine not loaded — call load() first")

        messages = data.get("messages")
        prompt = data.get("prompt")
        params = self._get_generation_params(data)
        params["stream"] = True

        if messages:
            stream = self.client.chat_completions_stream(
                messages=messages,
                model=self.config.model_path,
                **params,
            )
        elif prompt:
            stream = self.client.completions_stream(
                prompt=prompt,
                model=self.config.model_path,
                **params,
            )
        else:
            raise ValueError("data must contain 'messages' or 'prompt'")

        async for chunk in stream:
            if cancellation_event and cancellation_event.is_set():
                yield {"choices": [{"delta": {}, "finish_reason": "cancelled"}]}
                return
            yield chunk

    @override
    async def count_tokens_for_messages(
        self,
        messages_or_prompt: list[dict[str, Any]] | str,
        use_cpu: bool = True,
        context_length: int | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> TokenCountResult:
        """Count tokens via vLLM server /tokenize endpoint.

        For chat messages, uses the messages format with add_generation_prompt=True
        so the chat template is applied — matching the actual token count at inference.
        For raw prompts, uses the prompt format.
        Tools are included so the template expansion accounts for their token cost.
        """
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
            if isinstance(messages_or_prompt, list):
                count = await self.client.tokenize_messages(
                    messages_or_prompt,
                    model=self.config.model_path,
                    tools=tools,
                )
            else:
                tokens = await self.client.tokenize(
                    messages_or_prompt, model=self.config.model_path
                )
                count = len(tokens)
            return TokenCountResult(
                tokens=count,
                method="native_tokenizer",
                success=True,
            )
        except Exception as e:
            logger.warning("vLLM server tokenize failed: %s", e)
            raise

    @override
    def get_model_info(self) -> dict[str, Any]:
        """Return model information."""
        return {
            "engine_type": "vllm-server",
            "model_path": self.config.model_path,
            "tool_call_parser": self.config.tool_call_parser,
            "socket_path": self.config.socket_path,
        }

    @override
    def is_loaded(self) -> bool:
        """True if server process exists and is not definitively stopped.

        UNHEALTHY (transient health-check failure) still counts as loaded —
        the vLLM process is alive and can serve tokenize/generate requests.
        Only STOPPED or STOPPING mean the subprocess is gone.
        """
        if self._crashed:
            return False
        if not self.server_manager:
            return False
        return self.server_manager.status not in (
            ServerStatus.STOPPED,
            ServerStatus.STOPPING,
        )
