"""
VLLM engine model loading operations.

Handles model loading, tokenizer initialization, startup warmup, and unload.
"""

import gc
import time
from typing import Any

from transformers import AutoTokenizer
from universal_logging import get_logger
from vllm import AsyncLLMEngine
from vllm.engine.arg_utils import AsyncEngineArgs

logger = get_logger(__name__)


class VLLMModelLoader:
    """Handles model loading and unloading for VLLM engine."""

    def __init__(self, engine_instance: Any):
        """
        Initialize model loader with reference to engine instance.

        Args:
            engine_instance: The VLLMEngine instance to operate on
        """
        self.engine = engine_instance

    async def load(self) -> None:
        """Load model using vLLM AsyncLLMEngine with provided parameters."""
        if self.engine.loaded:
            logger.warning("Model already loaded")
            return

        try:
            # Disable Torch compilation to avoid Inductor errors
            # Note: Environment variables are set at module import time
            try:
                import torch

                torch._dynamo.config.disable = True
            except Exception as e:
                logger.debug(f"Could not disable torch._dynamo: {e}")

            # Detect quantization format if not explicitly specified
            explicit_quantization = self.engine.kwargs.get("quantization")
            if explicit_quantization:
                self.engine.quantization_format = explicit_quantization
                logger.info(
                    f"Using explicit quantization format: {self.engine.quantization_format}"
                )
            else:
                self.engine.quantization_format = (
                    self.engine._quantization_detector.detect_quantization()
                )
                if self.engine.quantization_format:
                    logger.info(
                        f"Auto-detected quantization format: {self.engine.quantization_format}"
                    )
                else:
                    logger.info(
                        "No quantization detected - loading as standard HuggingFace model"
                    )

            # Extract vLLM-specific parameters from kwargs
            vllm_params = self.engine._param_builder.extract_vllm_params()

            # Validate that all required parameters are provided (no defaults)
            self.engine._quantization_detector.validate_required_params(
                vllm_params, self.engine.quantization_format
            )

            # Add quantization parameter if detected
            if self.engine.quantization_format:
                vllm_params["quantization"] = self.engine.quantization_format
                logger.info(
                    f"Using quantization format: {self.engine.quantization_format}"
                )

            # Use only the parameters provided by the client
            final_params = vllm_params

            # Log the configuration being used
            logger.info(f"Loading model with vLLM parameters: {final_params}")

            # Create AsyncLLMEngine for both streaming and non-streaming support
            engine_args = AsyncEngineArgs(model=self.engine.model_path, **final_params)

            self.engine.llm = AsyncLLMEngine.from_engine_args(engine_args)

            # Load tokenizer for token counting
            try:
                self.engine.tokenizer = AutoTokenizer.from_pretrained(
                    self.engine.model_path,
                    trust_remote_code=vllm_params.get("trust_remote_code", False),
                    legacy=True,
                )
            except Exception as e:
                logger.warning(f"Could not load tokenizer: {e}")

            # Get model info
            self.engine.model_info = self.engine.get_model_info()

            # Mark loaded BEFORE warmup (warmup uses generate() which checks this)
            self.engine.loaded = True
            logger.info(f"vLLM model loaded successfully: {self.engine.model_path}")

            # Perform startup warmup to trigger PTX→SASS JIT compilation
            warmup_config = self.engine.kwargs.get("warmup", {})
            if warmup_config.get("enabled", False):
                _ = await self._perform_startup_warmup(warmup_config)
            else:
                logger.info(
                    "vLLM startup warmup disabled - first inference will trigger "
                    "PTX→SASS JIT compilation (~60-90s)"
                )

        except Exception as e:
            logger.error(f"Failed to load vLLM model: {e}")
            raise

    async def _perform_startup_warmup(self, warmup_config: dict[str, Any]) -> bool:
        """Perform startup warmup to trigger PTX→SASS JIT compilation.

        Runs one non-streaming inference with a minimal prompt so that
        NVIDIA driver kernel compilation happens before real user traffic.

        Optionally clears CUDA memory cache before/after warmup.
        Does NOT touch on-disk caches (VLLM_CACHE_ROOT, CUDA_CACHE_PATH).

        Args:
            warmup_config: Dict with keys: enabled, max_tokens, prompt_tokens,
                          clear_cuda_cache_before, clear_cuda_cache_after

        Returns:
            True if warmup succeeded, False if it failed (non-fatal)
        """
        max_tokens = warmup_config.get("max_tokens", 20)
        prompt_tokens = warmup_config.get("prompt_tokens", 50)
        clear_before = warmup_config.get("clear_cuda_cache_before", False)
        clear_after = warmup_config.get("clear_cuda_cache_after", True)

        warmup_start = time.time()
        logger.info(
            f"[vLLM warmup] Starting startup warmup "
            f"(max_tokens={max_tokens}, prompt_tokens≈{prompt_tokens})"
        )

        # Step 1: Clear CUDA cache before warmup (if configured)
        if clear_before:
            self._clear_cuda_cache("before warmup")

        # Step 2: Run one inference to compile CUDA kernels
        try:
            warmup_prompt = self._build_warmup_prompt(prompt_tokens)
            warmup_data: dict[str, Any] = {
                "messages": [{"role": "user", "content": warmup_prompt}],
                "max_tokens": max_tokens,
                "stream": False,
            }

            logger.info("[vLLM warmup] Running warmup inference...")
            _ = await self.engine._regular_inference.generate(warmup_data)

            elapsed = time.time() - warmup_start
            logger.info(
                f"[vLLM warmup] Startup warmup completed in {elapsed:.2f}s "
                f"(max_tokens={max_tokens})"
            )

        except Exception as e:
            elapsed = time.time() - warmup_start
            logger.warning(
                f"[vLLM warmup] Startup warmup failed after {elapsed:.2f}s "
                f"(non-fatal): {e}"
            )
            return False

        # Step 3: Clear CUDA cache after warmup (if configured)
        if clear_after:
            self._clear_cuda_cache("after warmup")

        return True

    @staticmethod
    def _build_warmup_prompt(target_tokens: int) -> str:
        """Build a minimal warmup prompt of approximately target_tokens length.

        Uses repeated short words; each repetition ≈ 2 tokens.

        Args:
            target_tokens: Approximate number of tokens desired

        Returns:
            Warmup prompt string
        """
        repetitions = max(1, target_tokens // 2)
        return " ".join(["warmup"] * repetitions)

    @staticmethod
    def _clear_cuda_cache(context: str) -> None:
        """Clear CUDA memory cache (not filesystem caches).

        Args:
            context: Description of when clearing happens (for logging)
        """
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                torch.cuda.synchronize()
                logger.info(f"[vLLM warmup] Cleared CUDA cache ({context})")
        except Exception as e:
            logger.warning(f"[vLLM warmup] Could not clear CUDA cache ({context}): {e}")

    async def unload(self) -> None:
        """Unload model and free resources, following GGUF engine pattern."""
        if not self.engine.loaded:
            return

        try:
            if self.engine.llm:
                # Try to properly shutdown VLLM engine and its subprocesses
                try:
                    # Check if VLLM has a shutdown method
                    if hasattr(self.engine.llm, "shutdown"):
                        logger.info("Calling VLLM shutdown method")
                        self.engine.llm.shutdown()
                    elif hasattr(self.engine.llm, "stop_remote_worker_execution_loop"):
                        logger.info("Stopping VLLM remote worker execution loop")
                        self.engine.llm.stop_remote_worker_execution_loop()
                    elif hasattr(self.engine.llm, "engine") and hasattr(
                        self.engine.llm.engine, "stop_remote_worker_execution_loop"
                    ):
                        logger.info("Stopping VLLM engine remote worker execution loop")
                        self.engine.llm.engine.stop_remote_worker_execution_loop()

                    # Force cleanup of VLLM engine core processes
                    if hasattr(self.engine.llm, "engine"):
                        engine = self.engine.llm.engine
                        if hasattr(engine, "workers"):
                            logger.info("Cleaning up VLLM workers")
                            for worker in engine.workers:
                                if hasattr(worker, "shutdown"):
                                    worker.shutdown()

                except Exception as cleanup_error:
                    logger.warning(f"VLLM cleanup method failed: {cleanup_error}")

                # Clear the reference
                self.engine.llm = None

            # Clear other references
            self.engine.tokenizer = None
            self.engine.model_info = {}
            self.engine.loaded = False

            # Force garbage collection to help free GPU memory
            gc.collect()

            # Try to clear CUDA cache if available
            try:
                import torch

                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
                    torch.cuda.synchronize()
                    logger.info("Cleared CUDA cache after VLLM unload")
            except Exception as cuda_error:
                logger.warning(f"Could not clear CUDA cache: {cuda_error}")

            logger.info(f"VLLM model unloaded: {self.engine.model_path}")

        except Exception as e:
            logger.error(f"Error unloading VLLM model: {e}")
            # Still mark as unloaded even if cleanup failed
            self.engine.loaded = False
