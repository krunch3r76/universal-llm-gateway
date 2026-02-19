"""RPC handlers for Flux.2 image generation."""

from typing import Any

from universal_logging import get_logger
from universal_protocol.errors import EngineError

logger = get_logger(__name__)


class FluxImageHandlers:
    """
    RPC handlers for Flux.2 image generation.

    Mixin for Worker class to handle generate_image RPC calls.
    """

    async def handle_generate_image(self, params: dict[str, Any]) -> dict[str, Any]:
        """Handle generate_image RPC for Flux.2 image generation."""
        if not self.engine or not self.engine.is_loaded():
            raise EngineError(code="MODEL_NOT_LOADED", message="Model not loaded")

        prompt = params.get("prompt")
        if not prompt:
            raise EngineError(code="INVALID_PARAMS", message="prompt required")

        width = params.get("width", 1024)
        height = params.get("height", 1024)
        num_inference_steps = params.get("num_inference_steps", 20)  # FLUX.2 default
        guidance_scale = params.get("guidance_scale", 4.0)  # FLUX.2 default
        seed = params.get("seed")
        response_format = params.get("response_format", "b64_json")
        caption_upsample_temperature = params.get("caption_upsample_temperature")

        logger.info(
            f"🎨 [worker] Generating image (FLUX.2): prompt='{prompt[:50]}...', "
            f"size={width}x{height}, steps={num_inference_steps}"
        )

        result = await self.engine.generate(
            {
                "prompt": prompt,
                "width": width,
                "height": height,
                "num_inference_steps": num_inference_steps,
                "guidance_scale": guidance_scale,
                "seed": seed,
                "response_format": response_format,
                "caption_upsample_temperature": caption_upsample_temperature,
            }
        )

        logger.info(
            f"✅ [worker] Image generation complete: "
            f"{len(result.get('data', []))} image(s)"
        )
        return result
