"""
GGUF inference engine implementation.

Core inference engine that focuses on loading models and generating responses.
Uses llama-cpp-python for inference without applying defaults.
"""

import asyncio
import time
from collections.abc import AsyncGenerator, Iterator
from typing import Any, TypeVar

from universal_logging import get_logger

T = TypeVar("T")

from inference_djinn.utils.types import TokenCountResult

logger = get_logger(__name__)

# =============================================================================
# CRITICAL: Load environment variables BEFORE any llama-cpp-python imports
# =============================================================================
# Engine env vars are defined in: libs/inference_djinn/config/engine_env.yaml
# This provides a single, documented config file for both Docker and baremetal.
try:
    from inference_djinn.config.env_loader import load_engine_env

    _applied_env = load_engine_env()  # Apply engine env vars from config

    # Debug logging: Print engine environment after loading
    if _applied_env:
        logger.info(
            f"[GGUF Engine] Loaded {len(_applied_env)} engine environment variables:"
        )
        for _key, _value in sorted(_applied_env.items()):
            logger.info(f"[GGUF Engine]   {_key}={_value}")
    else:
        logger.warning(
            "[GGUF Engine] No engine environment variables loaded from engine_env.yaml"
        )
except ImportError:
    # inference_djinn.config not available (shouldn't happen, but handle gracefully)
    logger.warning(
        "[GGUF Engine] Could not load engine environment variables (env_loader not available)"
    )

import inspect

# import gguf  # Only needed for metadata extraction, not inference
# os.sched_setaffinity(0, list(range(12))) # Set affinity to first 12 cores (power cores on ryzen 9 7900x)
import llama_cpp
from llama_cpp import Llama

# Check if this is the Blackwell-optimized version
if llama_cpp.__version__ == "9.9.9":
    logger.info("Using BLACKWELL-OPTIMIZED llama-cpp-python v9.9.9")
else:
    path = inspect.getfile(llama_cpp)
    if "optimized" in path.lower() or "blackwell" in path.lower():
        logger.info(
            f"Using BLACKWELL-OPTIMIZED llama-cpp-python v{llama_cpp.__version__}"
        )
    else:
        logger.info(f"Using STANDARD llama-cpp-python v{llama_cpp.__version__}")

from inference_djinn.engines.base import BaseEngine

from .formatting.prompt_builder import GGUFPromptBuilder
from .inference.parameters import GGUFParameterBuilder
from .inference.regular import GGUFRegularInference
from .inference.streaming import GGUFStreamingInference
from .loading import GGUFModelLoader
from .token_counting import GGUFTokenCounter
from .vision.types import MessageList


class GGUFEngine(BaseEngine):
    """
    GGUF inference engine using llama-cpp-python with configurable KV cache warmup.

    Phase 1: Unified Completion Path
    --------------------------------
    The engine uses a unified path for both messages and prompt-based requests,
    ensuring consistent behavior across input types. Both paths use create_chat_completion()
    or create_completion() with canonical formatted prompts.

    Phase 2: Warmup Refactor with Configurable Modes
    ------------------------------------------------
    The engine supports structured warmup configuration per request type (streaming/non-streaming).
    Each mode can independently configure:
    - enabled: Enable/disable warmup
    - mode: "minimal" (dummy prompt) or "full_prompt" (use actual request)
    - max_tokens: Override max_tokens for warmup generation
    - minimal_prompt_tokens: Token count for minimal mode dummy prompt
    - clear_kv_before: Clear KV cache before warmup
    - clear_kv_after: Clear KV cache after warmup (removes warmup pollution)

    Warmup Configuration:
        warmup (dict): Structured warmup config with streaming/non_streaming keys
            streaming (dict): Config for streaming requests
                enabled (bool): Enable warmup (default: False)
                mode (str): "minimal" or "full_prompt" (default: "minimal")
                max_tokens (int | None): Override max_tokens (default: None = use mode defaults)
                minimal_prompt_tokens (int): Tokens for minimal mode (default: 100)
                clear_kv_before (bool): Clear before warmup (default: True)
                clear_kv_after (bool): Clear after warmup (default: False)
            non_streaming (dict): Same structure as streaming

    Example:
        # Default: no warmup
        engine = GGUFEngine(model_path)

        # Minimal warmup for non-streaming only
        engine = GGUFEngine(
            model_path,
            warmup={
                "non_streaming": {
                    "enabled": True,
                    "mode": "minimal",
                    "minimal_prompt_tokens": 100,
                }
            }
        )

        # Full prompt warmup with max_tokens override
        engine = GGUFEngine(
            model_path,
            warmup={
                "streaming": {
                    "enabled": True,
                    "mode": "full_prompt",
                    "max_tokens": 1,
                    "clear_kv_after": True,
                }
            }
        )

    Note:
        create_chat_completion() doesn't support cache_prompt, add_generation_prompt,
        or penalize_eos parameters. These are only available in create_completion()
        for prompt-based generation.

        The unified completion path ensures consistent behavior between messages and prompt
        paths by using the appropriate completion method with canonical formatted prompts.
    """

    def __init__(self, model_path: str, **kwargs):
        super().__init__(model_path, **kwargs)
        self.llama_model: Llama | None = None
        self.engine_type = "gguf"
        self._chat_template_available = False  # Will be set during load()
        self._chat_template_required = False  # Will be set during load()

        # Embedding mode configuration
        self._embedding_mode = kwargs.get("embedding", False)

        # Embedding task prefix configuration (for models like Nomic)
        # task_default: default task if not specified per-request (e.g., "search_document")
        # task_prefixes: dict mapping task names to prefix strings
        self._embedding_task_default = kwargs.get("embedding_task_default")
        self._embedding_task_prefixes = kwargs.get("embedding_task_prefixes")

        # Warmup configuration (structured)
        warmup_config = kwargs.get("warmup", {})
        self.warmup_streaming = self._parse_warmup_mode_config(
            warmup_config.get("streaming", {})
        )
        self.warmup_non_streaming = self._parse_warmup_mode_config(
            warmup_config.get("non_streaming", {})
        )

        # Phase 1: Unified completion path configuration
        self.debug_token_level = kwargs.get("debug_token_level", False)

        # Phase 2: Parameter builder and seed configuration
        self.default_seed = kwargs.get("default_seed", None)

        # Initialize loading module
        self._loader = GGUFModelLoader(self)

        # Initialize parameter builder
        self._param_builder = GGUFParameterBuilder(self)

        # Initialize inference modules
        self._regular_inference = GGUFRegularInference(self)
        self._streaming_inference = GGUFStreamingInference(
            self, self._regular_inference
        )

        # Initialize formatting module
        self._prompt_builder = GGUFPromptBuilder(self)

        # Initialize token counter
        self._token_counter = GGUFTokenCounter(self)

        # Check llama-cpp-python version compatibility early
        self._check_llama_cpp_compatibility()

    def _parse_warmup_mode_config(self, config: dict) -> dict:
        """Parse warmup mode config with defaults.

        Returns dict with keys: enabled, mode, max_tokens, minimal_prompt_tokens,
                               clear_kv_before, clear_kv_after
        """
        return {
            "enabled": config.get("enabled", False),
            "mode": config.get("mode", "minimal"),
            "max_tokens": config.get("max_tokens"),  # None = use default
            "minimal_prompt_tokens": config.get("minimal_prompt_tokens", 100),
            "clear_kv_before": config.get("clear_kv_before", True),
            "clear_kv_after": config.get("clear_kv_after", False),
        }

    def _check_llama_cpp_compatibility(self) -> None:
        """
        Check if llama-cpp-python version supports required methods.
        Raises RuntimeError if incompatible version is detected.
        """
        try:
            # Try to import and check if Llama class has required methods
            from llama_cpp import Llama

            # Check if the required methods exist in the class definition
            required_methods = ["create_chat_completion", "create_completion"]
            missing_methods = [
                method for method in required_methods if not hasattr(Llama, method)
            ]

            if missing_methods:
                raise RuntimeError(
                    f"Unified completion path requires llama-cpp-python with methods: {required_methods}. "
                    f"Missing methods: {missing_methods}. "
                    "Please upgrade llama-cpp-python or use prompt-based requests instead."
                )

            logger.debug(
                "llama-cpp-python version is compatible with unified completion path"
            )

        except ImportError as e:
            raise RuntimeError(f"Failed to import llama_cpp: {e}")
        except Exception as e:
            raise RuntimeError(f"llama-cpp-python compatibility check failed: {e}")

    def _build_formatted_prompt(
        self, messages: MessageList | None, prompt: str | None
    ) -> str:
        """Delegate to prompt builder for formatted prompt building."""
        return self._prompt_builder.build_formatted_prompt(messages, prompt)

    def _validate_stop_list(self, stop: str | list[str] | None) -> list[str]:
        """Delegate to parameter builder for stop list validation."""
        return self._param_builder._validate_stop_list(stop)

    def _build_generation_params(
        self,
        raw_params: dict[str, Any],
        is_streaming: bool = False,
    ) -> dict[str, Any]:
        """Delegate to parameter builder for generation parameter building."""
        return self._param_builder.build_generation_params(raw_params, is_streaming)

    async def _create_completion_unified(
        self,
        messages: MessageList | None,
        prompt: str | None,
        generation_params: dict[str, Any],
        is_streaming: bool = False,
    ) -> dict[str, Any] | Iterator[dict[str, Any]]:
        """Delegate to regular inference for unified completion."""
        return await self._regular_inference._create_completion_unified(
            messages, prompt, generation_params, is_streaming
        )

    async def _perform_warmup(
        self,
        *,
        is_streaming: bool,
        messages: MessageList | None = None,
        prompt: str | None = None,
        request_max_tokens: int | None = None,
    ) -> None:
        """Perform warmup with KV cache management based on configuration.

        Handles both warmup and KV cache clearing in a unified method.
        Dispatches to minimal or full_prompt mode based on config.

        Args:
            is_streaming: True for streaming request, False for non-streaming
            messages: Actual request messages (for full_prompt mode)
            prompt: Actual request prompt (for full_prompt mode)
            request_max_tokens: Request's max_tokens (used as default for full_prompt)

        Pre: (mode = full_prompt) ⟹ (messages ∨ prompt) provided
        Post: KV cache in configured state, warmup completed (if enabled)

        Flow:
            1. clear_kv_before → clear_kv_cache() (if configured)
            2. warmup → generate with configured mode (if enabled)
            3. clear_kv_after → clear_kv_cache() (if configured)
        """
        # Select config based on request type
        config = self.warmup_streaming if is_streaming else self.warmup_non_streaming

        # Step 1: Clear KV cache before warmup (if configured)
        if config["clear_kv_before"]:
            self._clear_kv_cache_internal()

        # Step 2: Perform warmup (if enabled)
        if config["enabled"]:
            mode = config["mode"]

            try:
                warmup_start = time.time()

                # Build warmup content based on mode
                if mode == "full_prompt":
                    # Use actual request
                    if messages:
                        warmup_messages = messages
                    elif prompt:
                        warmup_messages = [{"role": "user", "content": prompt}]
                    else:
                        # Fallback to minimal if no request data
                        logger.warning(
                            "full_prompt mode but no request data provided, falling back to minimal"
                        )
                        warmup_messages = [
                            {
                                "role": "user",
                                "content": self._build_minimal_warmup_prompt(100),
                            }
                        ]
                else:
                    # Minimal mode: dummy prompt
                    token_count = config["minimal_prompt_tokens"]
                    warmup_messages = [
                        {
                            "role": "user",
                            "content": self._build_minimal_warmup_prompt(token_count),
                        }
                    ]

                # Determine max_tokens for warmup
                if config["max_tokens"] is not None:
                    # Explicit override
                    warmup_max_tokens = config["max_tokens"]
                elif mode == "full_prompt":
                    # Use request's max_tokens (or default 1)
                    warmup_max_tokens = request_max_tokens if request_max_tokens else 1
                else:
                    # Minimal mode default
                    warmup_max_tokens = 1

                warmup_params = {"max_tokens": warmup_max_tokens, "stream": False}

                logger.info(
                    f"🔥 [warmup] mode={mode}, is_streaming={is_streaming}, "
                    f"max_tokens={warmup_max_tokens}, prompt_len={len(str(warmup_messages))}"
                )

                await self._regular_inference._create_completion_unified(
                    warmup_messages, None, warmup_params, is_streaming=False
                )

                warmup_time = time.time() - warmup_start
                logger.info(f"🔥 [warmup] Completed in {warmup_time:.2f}s")

                # Synchronize CUDA state after warmup
                from .cuda_sync import synchronize_cuda

                sync_time = synchronize_cuda(self.llama_model)
                if sync_time is not None:
                    logger.debug(f"🔥 [warmup] CUDA synchronized in {sync_time:.3f}ms")

            except Exception as e:
                logger.warning(f"⚠️ [warmup] Failed (non-fatal): {e}")

        # Step 3: Clear KV cache after warmup (if configured)
        # This removes warmup pollution before actual generation
        if config["clear_kv_after"]:
            self._clear_kv_cache_internal()

    def _clear_kv_cache_internal(self) -> None:
        """Internal KV cache clearing - always executes.

        Unlike the old clear_kv_cache(), this doesn't check disable_kv_cache_clear.
        Configuration is now handled at the caller level via clear_kv_before/after.
        """
        if self.llama_model is None:
            return

        logger.debug("🔧 [GGUF] Clearing KV cache")
        self.llama_model.reset()
        self.llama_model._ctx.kv_cache_seq_rm(-1, 0, -1)

    def _build_minimal_warmup_prompt(self, token_count: int) -> str:
        """Build dummy warmup prompt with approximately token_count tokens.

        Uses "WARMUP " repeated - each repetition is ~1-2 tokens.
        Actual token count may vary by model's tokenizer.

        Args:
            token_count: Target token count (approximate)

        Returns:
            Dummy prompt string
        """
        # "WARMUP " is typically 1-2 tokens, use 1.5 as estimate
        repetitions = max(1, int(token_count / 1.5))
        return "WARMUP " * repetitions

    async def load(self) -> None:
        """Load GGUF model using provided configuration only"""
        await self._loader.load()

    async def _validate_chat_template_support(self) -> None:
        """Delegate to loader for chat template validation."""
        await self._loader._validate_chat_template_support()

    async def generate(
        self, data: dict[str, Any], cancellation_event: asyncio.Event | None = None
    ) -> dict[str, Any]:
        """Generate with optional cancellation support.

        Args:
            data: Generation parameters
            cancellation_event: Optional event that triggers abort when set

        Returns:
            Generation result dictionary
        """
        return await self._regular_inference.generate(data, cancellation_event)

    async def generate_stream(
        self, data: dict[str, Any], cancellation_event: asyncio.Event | None = None
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Delegate to streaming inference for streaming generation."""
        async for chunk in self._streaming_inference.generate_stream(
            data, cancellation_event
        ):
            yield chunk

    def _extract_llama_params(
        self, generation_params: dict[str, Any]
    ) -> dict[str, Any]:
        """Delegate to regular inference for llama parameter extraction."""
        return self._regular_inference._extract_llama_params(generation_params)

    # REVIEW FOR DEPRECATION
    async def unload(self) -> None:
        """Unload GGUF model and free resources"""
        await self._loader.unload()

    async def count_tokens_for_messages(
        self,
        messages_or_prompt: list[dict[str, Any]] | str,
        use_cpu: bool = True,
        context_length: int | None = None,  # For API compatibility
    ) -> TokenCountResult:
        """Delegate to token counter for token counting."""
        return await self._token_counter.count_tokens_for_messages(
            messages_or_prompt, use_cpu, context_length
        )

    @property
    def supports_vision(self) -> bool:
        """Check if the loaded model supports vision/multi-modal input."""
        vision_config = getattr(self, "_vision_config", None)
        return vision_config is not None and vision_config.is_vision_model

    def get_vision_info(self) -> dict[str, Any] | None:
        """Get vision model information if this is a vision model.

        Precedence for tokens_per_image:
        1. VisionConfig.tokens_per_image (from loader params / catalog)
        2. Registry default for architecture
        3. None (caller should use hardcoded fallback)
        """
        if not self.supports_vision:
            return None

        from .vision.registry import get_vision_model_info

        config = self._vision_config
        model_info = get_vision_model_info(config.vision_architecture)

        # Precedence: config override > registry default
        tokens_per_image = config.tokens_per_image
        if tokens_per_image is None and model_info:
            tokens_per_image = model_info.tokens_per_image

        return {
            "architecture": config.vision_architecture,
            "clip_model_path": config.clip_model_path,
            "tokens_per_image": tokens_per_image,
            "description": model_info.description if model_info else None,
        }

    def get_model_info(self) -> dict[str, Any]:
        """Get basic model information"""
        info = {
            "model_path": self.model_path,
            "loaded": self.loaded,
            "engine_type": self.engine_type,
        }

        # Add vision info if applicable
        vision_info = self.get_vision_info()
        if vision_info:
            info["vision"] = vision_info

        return info

    def clear_kv_cache(self) -> None:
        """
        Clear KV cache before each request.

        For vision models, standard reset() is insufficient as image embeddings
        persist in the KV cache. This method performs complete cache invalidation:
        1. reset() - resets token counter
        2. kv_cache_seq_rm(-1, 0, -1) - clears ALL sequences from pos 0 to end

        Pre: self.llama_model is not None (model loaded)
        Post: KV cache empty, ready for fresh inference

        Note: This is now a wrapper around _clear_kv_cache_internal().
        Per-request warmup configuration now handles KV cache clearing
        via clear_kv_before/after flags.
        """
        self._clear_kv_cache_internal()

    def create_embedding(
        self,
        input_texts: list[str],
        task: str | None = None,
    ) -> dict:
        """
        Generate embeddings for input texts.

        Strategy:
        1. Try native batch embedding (fast path)
        2. On failure: fall back to per-text embedding with result merging

        Args:
            input_texts: List of strings to embed
            task: Optional task name for prefix lookup (e.g., "search_document")

        Returns:
            OpenAI-compatible embedding response dict

        Raises:
            RuntimeError: If model not in embedding mode or not loaded
            ValueError: If task specified but not found in task_prefixes
        """
        if not self._embedding_mode:
            raise RuntimeError(
                "Model not initialized for embeddings. "
                "Set embedding=True in loader config."
            )

        if not self.loaded or self.llama_model is None:
            raise RuntimeError("Model not loaded")

        if not input_texts:
            raise ValueError("input_texts must be non-empty")

        # Apply task prefix if configured
        texts_to_embed = self._apply_task_prefix(input_texts, task)

        # Try native batch embedding first (fast path)
        if len(texts_to_embed) > 1:
            try:
                result = self.llama_model.create_embedding(texts_to_embed)
                # Validate result shape
                if isinstance(result.get("data"), list) and len(result["data"]) == len(texts_to_embed):
                    return result
                # Shape mismatch - fall through to per-text
                logger.warning(
                    f"Batch embedding returned wrong count: expected {len(texts_to_embed)}, "
                    f"got {len(result.get('data', []))}. Falling back to per-text."
                )
            except Exception as e:
                logger.warning(f"Batch embedding failed: {e}. Falling back to per-text.")

        # Per-text embedding (fallback or single input)
        return self._create_embedding_per_text(texts_to_embed)

    def _apply_task_prefix(
        self,
        input_texts: list[str],
        task: str | None,
    ) -> list[str]:
        """Apply task prefix to input texts if configured."""
        effective_task = task or self._embedding_task_default

        if effective_task and self._embedding_task_prefixes:
            prefix = self._embedding_task_prefixes.get(effective_task)
            if prefix is None:
                logger.warning(
                    f"Unknown embedding task '{effective_task}', "
                    f"available: {list(self._embedding_task_prefixes.keys())}. "
                    f"Proceeding without prefix."
                )
                return input_texts
            logger.debug(
                f"Applied embedding task prefix: '{prefix}' for task '{effective_task}'"
            )
            return [f"{prefix}{text}" for text in input_texts]
        elif effective_task:
            logger.warning(
                f"Embedding task '{effective_task}' specified but no task_prefixes configured"
            )

        return input_texts

    def _create_embedding_per_text(self, texts: list[str]) -> dict:
        """
        Generate embeddings one text at a time and merge results.

        INVARIANT: Always succeeds if single-text embedding works.

        Args:
            texts: Pre-processed texts (with prefixes applied)

        Returns:
            OpenAI-compatible embedding response with merged results
        """
        all_data = []
        total_tokens = 0

        for idx, text in enumerate(texts):
            result = self.llama_model.create_embedding(text)

            # Extract embedding data
            data_item = result["data"][0]
            all_data.append({
                "object": "embedding",
                "embedding": data_item["embedding"],
                "index": idx,
            })

            # Accumulate tokens
            usage = result.get("usage", {})
            total_tokens += usage.get("total_tokens", 0)

        return {
            "object": "list",
            "data": all_data,
            "model": self.model_path,
            "usage": {
                "prompt_tokens": total_tokens,
                "total_tokens": total_tokens,
            },
        }
