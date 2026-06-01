"""
ExLlamaV3 inference engine implementation.

Core inference engine that uses ExLlamaV3 for optimized GPTQ model inference.
Provides better architecture support and improved device handling compared to ExLlamaV2.
Supports EXL3 quantization format and addresses embedding.embedding=None errors.
"""

import asyncio
import importlib.util
from collections.abc import AsyncGenerator
from typing import Any

from universal_logging import get_logger

logger = get_logger(__name__)

exllamav3_available = importlib.util.find_spec("exllamav3") is not None

from ..base import BaseEngine  # noqa: E402
from .engine.inference.regular import ExLlamaV3RegularInference  # noqa: E402
from .engine.inference.streaming import ExLlamaV3StreamingInference  # noqa: E402
from .engine.loading import ExLlamaV3ModelLoader  # noqa: E402

# CancellationToken removed - using asyncio.Event directly


class ExLlamaV3Engine(BaseEngine):
    """ExLlamaV3 inference engine for GPTQ models"""

    def __init__(self, model_path: str, **kwargs):
        super().__init__(model_path, **kwargs)
        self.model = None
        self.tokenizer = None
        self.generator = None
        self.config = None
        self.engine_type = "exllamav3"

        # ExLlamaV3-specific configuration
        self.max_seq_len = kwargs.get("max_seq_len", 2048)
        self.max_input_len = kwargs.get("max_input_len", 2048)
        self.max_attention_size = kwargs.get("max_attention_size", 2048**2)

        # Store analysis results for runtime use (informational only)
        self._analysis_cache = None

        # Initialize modules
        self._loader = ExLlamaV3ModelLoader(self)
        self._regular_inference = ExLlamaV3RegularInference(self)
        self._streaming_inference = ExLlamaV3StreamingInference(
            self, self._regular_inference
        )

    async def load(self) -> None:
        """Load ExLlamaV3 model using provided configuration only"""
        await self._loader.load()

    def _get_model_analysis(self) -> dict[str, Any]:
        """Get cached model analysis (informational only)"""
        if self._analysis_cache is None:
            from .inspector import get_model_info_summary

            self._analysis_cache = get_model_info_summary(self.model_path)
        return self._analysis_cache

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

    async def _generate_with_exllamav3_stream(
        self, prompt: str, max_new_tokens: int, settings: dict[str, Any]
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Internal method to generate streaming with ExLlamaV3"""
        # ExLlamaV3 has a simplified generation API
        # This is a placeholder for the actual API calls
        try:
            # Create generation settings object if available
            if hasattr(self.generator, "generate_stream"):
                # Use native streaming if available
                async for chunk in self.generator.generate_stream(
                    prompt=prompt, max_new_tokens=max_new_tokens, **settings
                ):
                    if chunk:
                        yield {
                            "type": "token",
                            "content": chunk,
                            "token_count": 1,
                            "finish_reason": None,
                            "usage": {
                                "prompt_tokens": 0,
                                "completion_tokens": 1,
                                "total_tokens": 1,
                            },
                            "model_id": self._get_model_name(),
                        }
            elif hasattr(self.generator, "generate"):
                # Fallback to non-streaming generation if streaming not available
                result = await asyncio.to_thread(
                    self.generator.generate,
                    prompt=prompt,
                    max_new_tokens=max_new_tokens,
                    **settings,
                )

                if result:
                    yield {
                        "type": "token",
                        "content": result,
                        "token_count": 1,
                        "finish_reason": None,
                        "usage": {
                            "prompt_tokens": 0,
                            "completion_tokens": 1,
                            "total_tokens": 1,
                        },
                        "model_id": self._get_model_name(),
                    }
            else:
                # Try basic generation pattern
                # This will need to be updated based on actual ExLlamaV3 API
                encoded = await asyncio.to_thread(self.tokenizer.encode, prompt)
                output_tokens = await asyncio.to_thread(
                    self.generator.generate_tokens, encoded, max_new_tokens, **settings
                )
                result = await asyncio.to_thread(self.tokenizer.decode, output_tokens)

                if result:
                    yield {
                        "type": "token",
                        "content": result,
                        "token_count": 1,
                        "finish_reason": None,
                        "usage": {
                            "prompt_tokens": 0,
                            "completion_tokens": 1,
                            "total_tokens": 1,
                        },
                        "model_id": self._get_model_name(),
                    }
        except Exception as e:
            raise RuntimeError(f"ExLlamaV3 streaming generation failed: {e}") from e

    def _generate_with_exllamav3(
        self, prompt: str, max_new_tokens: int, settings: dict[str, Any]
    ) -> str:
        """Internal method to generate with ExLlamaV3"""
        # ExLlamaV3 has a simplified generation API
        # This is a placeholder for the actual API calls
        try:
            # Create generation settings object if available
            if hasattr(self.generator, "generate"):
                result = self.generator.generate(
                    prompt=prompt, max_new_tokens=max_new_tokens, **settings
                )
                return result
            elif hasattr(self.generator, "generate_simple"):
                # Fallback to simple generation if available
                result = self.generator.generate_simple(
                    prompt, max_new_tokens, **settings
                )
                return result
            else:
                # Try basic generation pattern
                # This will need to be updated based on actual ExLlamaV3 API
                encoded = self.tokenizer.encode(prompt)
                output_tokens = self.generator.generate_tokens(
                    encoded, max_new_tokens, **settings
                )
                return self.tokenizer.decode(output_tokens)
        except Exception as e:
            raise RuntimeError(f"ExLlamaV3 generation failed: {e}")

    def _count_tokens(self, text: str) -> int:
        """Count tokens in text using ExLlamaV3 tokenizer"""
        try:
            tokens = self.tokenizer.encode(text)
            return len(tokens) if isinstance(tokens, list) else tokens.shape[0]
        except Exception:
            # Fallback to basic estimation
            return len(text.split())

    def _extract_exllamav3_params(
        self, generation_params: dict[str, Any]
    ) -> dict[str, Any]:
        """Extract parameters for ExLlamaV3 generation - NO DEFAULTS APPLIED"""
        gen_kwargs = {}

        # Map common parameters to ExLlamaV3 equivalents
        param_mapping = {
            "max_tokens": "max_new_tokens",
            "temperature": "temperature",
            "top_p": "top_p",
            "top_k": "top_k",
            "repetition_penalty": "repetition_penalty",
            "length_penalty": "length_penalty",
            "no_repeat_ngram_size": "no_repeat_ngram_size",
            "num_beams": "num_beams",
            "early_stopping": "early_stopping",
        }

        # Handle parameter remapping
        for param_key, exllamav3_key in param_mapping.items():
            if param_key in generation_params:
                gen_kwargs[exllamav3_key] = generation_params[param_key]

        # Handle stop sequences
        stop_sequences = generation_params.get("stop", [])
        if isinstance(stop_sequences, str):
            stop_sequences = [stop_sequences]
        if stop_sequences:
            gen_kwargs["stop_sequences"] = stop_sequences

        # Pass through all other parameters that weren't remapped
        # Let ExLlamaV3 handle parameter validation
        for param_key, param_value in generation_params.items():
            if param_key not in param_mapping and param_key != "stop":
                gen_kwargs[param_key] = param_value

        return gen_kwargs

    async def unload(self) -> None:
        """Unload ExLlamaV3 model and free GPU memory"""
        await self._loader.unload()

    async def count_tokens_for_messages(
        self,
        messages_or_prompt: list[dict[str, Any]] | str,
        use_cpu: bool = True,
        context_length: int | None = None,
        tools: list[dict[str, Any]] | None = None,
    ):
        """
        Count tokens for chat messages or prompt string using ExLlamaV3 tokenizer.

        Args:
            messages_or_prompt: Either a list of chat messages or a prompt string
            use_cpu: If True, use CPU-based token counting. If False, use GPU-based counting.
            context_length: Context length for validation

        Returns:
            Token count result with method used and success status

        Raises:
            RuntimeError: If CPU counting is not supported and use_cpu=True
            RuntimeError: If GPU counting is not supported and use_cpu=False
        """
        if not self.loaded or not self.tokenizer:
            raise RuntimeError("Model must be loaded before counting tokens")

        if isinstance(messages_or_prompt, list):
            messages = messages_or_prompt
            if not messages:
                return {"tokens": 0, "method": "exllamav3_tokenizer", "success": True}

            # Format messages for tokenization
            formatted_prompt = ""
            for msg in messages:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                formatted_prompt += f"<|im_start|>{role}\n{content}<|im_end|>\n"
            formatted_prompt += "<|im_start|>assistant\n"
        elif isinstance(messages_or_prompt, str):
            formatted_prompt = messages_or_prompt
            if not formatted_prompt:
                return {"tokens": 0, "method": "exllamav3_tokenizer", "success": True}
        else:
            raise ValueError(
                f"messages_or_prompt must be List[Dict[str, str]] or str, got {type(messages_or_prompt)}"
            )

        import time

        start_time = time.time()

        try:
            if use_cpu:
                # CPU-based token counting is not supported for ExLlamaV3
                # ExLlamaV3 requires GPU for tokenization
                raise RuntimeError(
                    "CPU token counting is not supported for ExLlamaV3 models. ExLlamaV3 requires GPU for tokenization."
                )
            else:
                # GPU-based token counting (default for ExLlamaV3)
                logger.info("🔍 [ExLlamaV3] Using GPU-based token counting")

                # Use the existing _count_tokens method
                token_count = self._count_tokens(formatted_prompt)

                time_taken = time.time() - start_time

                return {
                    "tokens": token_count,
                    "method": "gpu_tokenizer",
                    "success": True,
                    "time_taken": time_taken,
                    "formatted_prompt": formatted_prompt[:200] + "..."
                    if len(formatted_prompt) > 200
                    else formatted_prompt,
                }

        except Exception as e:
            logger.error(f"Token counting failed: {e}")
            raise RuntimeError(f"Cannot count tokens for messages: {e}")

    def get_model_info(self) -> dict[str, Any]:
        """Get model information including analysis results"""
        analysis = self._get_model_analysis()
        return {
            **analysis,
            "loaded": self.loaded,
            "engine_instance": self.engine_type,
            "exllamav3_config": {
                "max_seq_len": self.max_seq_len,
                "max_input_len": self.max_input_len,
                "supports_exl3": True,
                "improved_device_handling": True,
            }
            if self.loaded
            else None,
        }
