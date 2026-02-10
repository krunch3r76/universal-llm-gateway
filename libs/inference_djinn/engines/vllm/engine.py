"""
vLLM Engine for inference_djinn.

High-performance inference engine using vLLM for HuggingFace models.
Supports standard HuggingFace models as well as AWQ and GPTQ quantized models.

Optimized for RTX 5090 (SM_120 / Blackwell architecture):
- Compilation disabled via TORCH_COMPILE_DISABLE=1 environment variable
- Sets optimal environment variables for SM_120
- Uses Flash Attention backend (not Triton version)

See: diagnostics/vllm/TORCH_INDUCTOR_TROUBLESHOOTING.md for details
"""

import os
import asyncio
from universal_logging import get_logger
# =============================================================================
# CRITICAL: Load environment variables BEFORE any vLLM/PyTorch imports
# =============================================================================
# Engine env vars are defined in: libs/inference_djinn/config/engine_env.yaml
# This provides a single, documented config file for both Docker and baremetal.
from inference_djinn.config.env_loader import load_engine_env  # noqa: E402

_applied_env = load_engine_env()  # Apply engine env vars from config

# Debug logging: Print engine environment after loading
_logger = get_logger(__name__)
if _applied_env:
    _logger.info(
        f"[vLLM Engine] Loaded {len(_applied_env)} engine environment variables:"
    )
    for _key, _value in sorted(_applied_env.items()):
        _logger.info(f"[vLLM Engine]   {_key}={_value}")
else:
    _logger.warning(
        "[vLLM Engine] No engine environment variables loaded from engine_env.yaml"
    )

# Cache vLLM env vars so they persist across fork to EngineCore subprocess
# (vLLM clears environment after fork, but cached values are inherited)
try:
    from vllm.envs import enable_envs_cache

    enable_envs_cache()
except ImportError:
    pass  # vLLM not installed, will be caught later

# NOTE: vLLM V0 has been removed from vLLM (post commit e19bce40a)
# vllm.engine.async_llm_engine.AsyncLLMEngine is now just an alias to V1
# Setting VLLM_USE_V1="0" will cause vLLM to raise an error internally
# because the AsyncLLM class has a guard that rejects envs.VLLM_USE_V1=False
#
# Historical context:
# - V0 was previously used for stability with GPTQ MoE models (Mixtral)
# - That issue may have been fixed in newer vLLM versions
# - VLLM_USE_V1="0" is now treated as "1" internally (V0 doesn't exist)
#
# We now validate and reject VLLM_USE_V1="0" before vLLM initialization
# to provide a clear error message to external tools (e.g., vllm_model_config_generator.py)

import asyncio
import importlib.util
from universal_logging import get_logger
import time
from collections.abc import AsyncGenerator
from typing import Any

from inference_djinn.engines.base import BaseEngine
from inference_djinn.utils.types import TokenCountResult

from .engine.formatting.prompt_builder import VLLMPromptBuilder
from .engine.inference.parameters import VLLMParameterBuilder
from .engine.inference.regular import VLLMRegularInference
from .engine.inference.streaming import VLLMStreamingInference
from .engine.loading import VLLMModelLoader
from .engine.quantization import VLLMQuantizationDetector

logger = get_logger(__name__)

# Try to import vLLM (needed for availability check and type hints)
try:
    from vllm import AsyncLLMEngine

    vllm_available = True
except ImportError as e:
    vllm_available = False
    logger.warning(f"vLLM not available - engine will not function: {e}", exc_info=True)

    # Create dummy class for type hints when vLLM is not available
    class AsyncLLMEngine:
        def __init__(self, **kwargs):
            pass


# Try to import transformers for tokenizer (needed for availability check)
transformers_available = importlib.util.find_spec("transformers") is not None
if not transformers_available:
    logger.warning("Transformers not available - token counting will be limited")


class VLLMEngine(BaseEngine):
    """vLLM-based inference engine for HuggingFace models with streaming support.

    Supports:
    - Standard HuggingFace models (safetensors/pytorch)
    - AWQ quantized models (4-bit)
    - GPTQ quantized models (2/3/4/8-bit)
    """

    def __init__(self, model_path: str, **kwargs):
        super().__init__(model_path, **kwargs)
        self.engine_type = "vllm"
        self.llm = None
        self.tokenizer = None
        self.model_info = {}
        self.quantization_format = None  # Will be detected or specified

        # Check if VLLM_USE_V1="0" is set - this will cause vLLM to fail
        # V0 has been removed from vLLM (post commit e19bce40a)
        vllm_use_v1 = os.environ.get("VLLM_USE_V1", "1")
        if vllm_use_v1 == "0":
            raise ValueError(
                "VLLM_USE_V1='0' is not supported. "
                "vLLM V0 has been removed (post commit e19bce40a). "
                "AsyncLLMEngine is now an alias to V1, and setting VLLM_USE_V1='0' "
                "causes vLLM to raise an error internally. "
                "Please remove VLLM_USE_V1='0' from your environment or set it to '1'. "
                "Note: The historical GPTQ Mixtral issue may have been fixed in newer vLLM versions."
            )

        if not vllm_available:
            raise ImportError(
                "vLLM is not available. Please install vLLM to use this engine."
            )

        # Initialize modules
        self._quantization_detector = VLLMQuantizationDetector(self)
        self._loader = VLLMModelLoader(self)
        self._param_builder = VLLMParameterBuilder(self)
        self._prompt_builder = VLLMPromptBuilder(self)
        self._regular_inference = VLLMRegularInference(self)
        self._streaming_inference = VLLMStreamingInference(self)

    async def load(self) -> None:
        """Load model using vLLM AsyncLLMEngine with provided parameters."""
        await self._loader.load()

    async def generate(
        self, data: dict[str, Any], cancellation_event: asyncio.Event | None = None
    ) -> dict[str, Any]:
        """Delegate to regular inference for non-streaming generation."""
        return await self._regular_inference.generate(data, cancellation_event)

    async def generate_stream(
        self, data: dict[str, Any], cancellation_event: asyncio.Event | None = None
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Delegate to streaming inference for streaming generation."""
        async for chunk in self._streaming_inference.generate_stream(
            data, cancellation_event
        ):
            yield chunk

    async def unload(self) -> None:
        """Unload model and free resources, following GGUF engine pattern."""
        await self._loader.unload()

    def get_model_info(self) -> dict[str, Any]:
        """Get model information including quantization details."""
        if not self.loaded:
            return {"status": "not_loaded"}

        info = {
            "model_path": self.model_path,
            "engine_type": self.engine_type,
            "loaded": self.loaded,
            "vllm_available": vllm_available,
            "transformers_available": transformers_available,
            "quantization_format": self.quantization_format,
            "quantization_supported": self.quantization_format in ["awq", "gptq"],
        }

        # Add tokenizer info if available
        if self.tokenizer:
            info["tokenizer"] = {
                "model_max_length": getattr(self.tokenizer, "model_max_length", None),
                "vocab_size": getattr(self.tokenizer, "vocab_size", None),
                "has_chat_template": hasattr(self.tokenizer, "apply_chat_template")
                and bool(self.tokenizer.chat_template),
            }

        # Add quantization-specific info
        if self.quantization_format:
            info["quantization_info"] = {
                "format": self.quantization_format,
                "hardware_requirements": {
                    "awq": "Turing (RTX 20xx) or newer",
                    "gptq": "Volta (V100) or newer",
                }.get(self.quantization_format, "N/A"),
            }

        return info

    async def count_tokens_for_messages(
        self,
        messages_or_prompt: list[dict[str, Any]] | str,
        use_cpu: bool = True,  # Ignored for VLLM (GPU-only)
        context_length: int | None = None,
    ) -> TokenCountResult:
        """
        Count tokens for either messages or formatted prompt using GPU tokenizer.

        Note: VLLM is GPU-only, use_cpu parameter is ignored.

        Args:
            messages_or_prompt: Either formatted prompt or message list
            use_cpu: Ignored (VLLM is GPU-only)
            context_length: Context length for validation

        Returns:
            TokenCountResult with count, method, and success status
        """
        if not self.tokenizer:
            raise RuntimeError("Tokenizer not available for token counting")

        start_time = time.time()

        try:
            # Step 1: Convert input to prompt string
            if isinstance(messages_or_prompt, str):
                prompt = messages_or_prompt
                input_type = "prompt"
            elif isinstance(messages_or_prompt, list):
                # Convert messages using chat template (no weak fallback)
                if (
                    hasattr(self.tokenizer, "apply_chat_template")
                    and self.tokenizer.chat_template
                ):
                    prompt = self.tokenizer.apply_chat_template(
                        messages_or_prompt, tokenize=False, add_generation_prompt=False
                    )
                    input_type = "messages"
                else:
                    raise ValueError(
                        "Model tokenizer lacks chat template support. "
                        "Use formatted prompt instead of messages."
                    )
            else:
                raise ValueError(
                    f"Expected str or list, got {type(messages_or_prompt)}"
                )

            # Step 2: GPU tokenization
            tokens = self.tokenizer.encode(prompt)
            token_count = len(tokens)
            time_taken = time.time() - start_time

            # Step 3: Context validation
            if context_length and token_count > context_length:
                logger.warning(
                    f"Token count ({token_count}) exceeds context length ({context_length})"
                )

            return TokenCountResult(
                tokens=token_count,
                method=f"vllm_tokenizer_{input_type}",
                success=True,
                time_taken=time_taken,
            )

        except Exception as e:
            logger.error(f"VLLM token counting error: {e}")
            return TokenCountResult(
                tokens=0,
                method="vllm_tokenizer_error",
                success=False,
                error=str(e)[:500],
                time_taken=time.time() - start_time,
            )
