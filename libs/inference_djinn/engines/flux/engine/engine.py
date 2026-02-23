"""
FLUX.2 image generation engine implementing BaseEngine interface.

Supports:
- Text-to-image generation via FLUX.2 with Mistral3 text encoder and MMDiT
- Caption upsampling for improved prompt adherence
- CPU offloading for text encoder and VAE (memory optimization)
- Multiple image sizes and quality settings
- Reproducible generation via seed control
"""

import asyncio
import time
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

from universal_logging import get_logger

from inference_djinn.engines.base import BaseEngine

from .formatting import format_image_response
from .inference import run_diffusion_pipeline
from .params import extract_generation_params

logger = get_logger(__name__)


class FluxEngine(BaseEngine):
    """
    FLUX.2 text-to-image engine implementing BaseEngine interface.

    Uses Flux2Pipeline with Mistral3 text encoder and MMDiT architecture.
    Supports caption upsampling for improved prompt adherence.

    Supports:
    - FLUX.2 models with Mistral3/MMDiT architecture
    - CPU offloading for memory optimization
    - Configurable inference steps and guidance (default: 4.0)
    - Caption upsample temperature for prompt enhancement
    - Multiple output formats (base64, URL)
    """

    def __init__(self, model_path: str, **kwargs):
        """
        Initialize FLUX.2 engine.

        Args:
            model_path: Path to FLUX.2 model directory
            **kwargs: Additional configuration:
                - device: Device to use ("cuda" or "cpu", default: "cuda")
                - cpu_offload: Enable CPU offloading for text encoder/VAE (default: False)
                - torch_dtype: Torch dtype (default: "float16")
                - variant: Model variant (default: None - use default model files)
        """
        super().__init__(model_path, **kwargs)
        self.engine_type = "flux"
        self.device = kwargs.get("device", "cuda")
        self.cpu_offload = kwargs.get("cpu_offload", False)
        self.torch_dtype_str = kwargs.get("torch_dtype", "float16")
        self.variant = kwargs.get("variant", None)
        self.pipe = None

    async def load(self) -> None:
        """Load FLUX.2 model and components."""
        if self.loaded:
            logger.warning("FLUX.2 model already loaded")
            return

        logger.info(f"Loading FLUX.2 model from {self.model_path}")
        start_time = time.time()

        try:
            # Import here to avoid loading heavy dependencies at module level
            import torch
            from diffusers import Flux2Pipeline

            # Clear GPU cache to avoid fragmentation issues
            # NOTE: empty_cache() is process-specific and only affects this worker process.
            # Other worker processes (other models) are unaffected since each runs in isolation.
            if torch.cuda.is_available():
                # Clear cache to reduce fragmentation before loading large model
                # This only affects the current PyTorch process, not other GPU processes
                torch.cuda.empty_cache()
                torch.cuda.synchronize()  # Ensure all operations complete before proceeding
                logger.debug(
                    "Cleared GPU cache for this process (does not affect other processes)"
                )

            # Convert torch_dtype string to torch dtype
            torch_dtype = getattr(torch, self.torch_dtype_str)

            # Load pipeline
            model_path_obj = Path(self.model_path)
            logger.info(f"Loading FLUX.2 pipeline from {model_path_obj}")

            # Build kwargs for from_pretrained
            load_kwargs = {
                "torch_dtype": torch_dtype,
                "local_files_only": True,  # Force local loading, no HF Hub downloads
            }
            # Only pass variant if explicitly specified (not None)
            if self.variant is not None:
                load_kwargs["variant"] = self.variant

            self.pipe = Flux2Pipeline.from_pretrained(
                str(model_path_obj),
                **load_kwargs,
            )

            # Apply CPU offloading if requested
            if self.cpu_offload:
                logger.info(
                    "Enabling model CPU offload (keeps transformer on GPU, offloads encoders/VAE)"
                )
                # Use enable_model_cpu_offload which is more balanced than sequential
                # It keeps transformer on GPU, offloads text encoders and VAE to CPU
                self.pipe.enable_model_cpu_offload()
                # Verify VRAM usage after offload
                if torch.cuda.is_available():
                    vram_used = torch.cuda.memory_allocated(0) / 1024**3
                    logger.info(f"VRAM usage after CPU offload: {vram_used:.2f} GB")
            else:
                logger.info(f"Moving pipeline to {self.device} (full GPU loading)")
                self.pipe = self.pipe.to(self.device)
                # Verify VRAM usage after full GPU load
                if torch.cuda.is_available():
                    vram_used = torch.cuda.memory_allocated(0) / 1024**3
                    logger.info(f"VRAM usage after full GPU load: {vram_used:.2f} GB")

            self.loaded = True
            elapsed = time.time() - start_time
            logger.info(f"FLUX.2 model loaded successfully in {elapsed:.2f}s")

        except Exception as e:
            logger.error(f"Failed to load FLUX.2 model: {e}")
            raise

    async def generate(
        self, data: dict[str, Any], cancellation_event: asyncio.Event | None = None
    ) -> dict[str, Any]:
        """
        Generate image from prompt.

        Args:
            data: Generation parameters:
                - prompt (str): Text prompt for image generation
                - num_inference_steps (int): Number of denoising steps (default: 20)
                - guidance_scale (float): Guidance strength (default: 4.0 for FLUX.2)
                - caption_upsample_temperature (float|None): Temperature for caption
                    upsampling (FLUX.2 feature for improved prompt adherence)
                - seed (int | None): Random seed for reproducibility
                - width (int): Image width (default: 1024)
                - height (int): Image height (default: 1024)
                - response_format (str): "b64_json" or "url" (default: "b64_json")
            cancellation_event: Event to signal cancellation

        Returns:
            dict with:
                - created (int): Unix timestamp
                - data (list): List of image results
                    - url (str): Base64 data URL or HTTP URL

        Raises:
            RuntimeError: If model not loaded
            ValueError: If required parameters missing
            Exception: If generation fails
        """
        if not self.loaded:
            raise RuntimeError("FLUX.2 model not loaded")

        try:
            # Extract and validate parameters
            params = extract_generation_params(data)

            # Run diffusion pipeline
            image = await run_diffusion_pipeline(self.pipe, params, self.device)

            # Format response
            return format_image_response(image, params.response_format)

        except Exception as e:
            logger.error(f"Image generation failed: {e}")
            raise

    async def unload(self) -> None:
        """Unload model from memory."""
        if not self.loaded:
            return

        logger.info("Unloading FLUX.2 model")

        try:
            if self.pipe:
                # Move components to CPU before deletion (prevents CUDA memory leaks)
                try:
                    logger.debug("Moving pipeline to CPU for cleanup")
                    self.pipe.to("cpu")
                except Exception as e:
                    logger.warning(f"Failed to move pipeline to CPU: {e}")

                del self.pipe
                self.pipe = None

            # Clear CUDA cache
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                torch.cuda.synchronize()  # Ensure CUDA operations complete

            self.loaded = False
            logger.info("FLUX.2 model unloaded successfully")

        except Exception as e:
            logger.error(f"Error unloading FLUX.2 model: {e}")
            raise

    async def generate_stream(
        self, data: dict[str, Any], cancellation_event: asyncio.Event | None = None
    ) -> AsyncGenerator[dict[str, Any], None]:
        """
        Streaming not supported for image generation.

        Flux generates complete images, not incremental outputs.
        Use generate() for image generation.
        """
        raise NotImplementedError(
            "FLUX image generation does not support streaming. "
            "Images are generated as complete outputs. Use generate() instead."
        )
        # Yield statement required for AsyncGenerator return type
        yield {}  # pragma: no cover

    def get_model_info(self) -> dict[str, Any]:
        """Get FLUX.2 model information."""
        return {
            "engine_type": self.engine_type,
            "model_path": self.model_path,
            "device": self.device,
            "cpu_offload": self.cpu_offload,
            "torch_dtype": self.torch_dtype_str,
            "variant": self.variant,
            "loaded": self.loaded,
            "supports_streaming": False,
            "supports_chat": False,
            "input_schema": "prompt",
            "output_type": "image",
        }

    async def count_tokens_for_messages(
        self,
        messages_or_prompt: list[dict[str, Any]] | str,
        use_cpu: bool = True,
        context_length: int | None = None,
        tools: list[dict[str, Any]] | None = None,
    ):
        """
        Not applicable for image generation models.

        Flux uses prompts with max_sequence_length=512, but this is handled
        internally by the diffusers pipeline, not via external token counting.
        """
        raise NotImplementedError(
            "Token counting not applicable for image generation models. "
            "FLUX prompt length is managed internally by the diffusers pipeline."
        )
