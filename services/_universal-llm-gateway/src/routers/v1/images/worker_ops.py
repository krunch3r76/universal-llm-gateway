"""Worker operations for image generation."""

import uuid

from fastapi import HTTPException
from universal_logging import get_logger

from .schemas import ImageGenerationRequest, ImageGenerationResponse

logger = get_logger(__name__)

# OpenAI-compatible quality/style presets for FLUX.2
# FLUX.2 converges efficiently with fewer steps
# FLUX.2 performs better at lower guidance values
QUALITY_MAPPING = {
    "standard": 20,  # Fast, good quality
    "hd": 50,  # Higher detail
}

STYLE_MAPPING = {
    "vivid": 4.0,  # Strong prompt adherence (FLUX.2 default)
    "natural": 2.5,  # More creative freedom
}


def resolve_generation_params(
    request: ImageGenerationRequest, width: int, height: int
) -> dict:
    """
    Resolve generation parameters with two-level control:
    1. Explicit params take precedence
    2. Fall back to quality/style mapping defaults
    """
    # Determine num_inference_steps
    if request.num_inference_steps is not None:
        num_inference_steps = request.num_inference_steps
    elif request.quality is not None:
        num_inference_steps = QUALITY_MAPPING.get(request.quality, 20)
    else:
        num_inference_steps = 20  # FLUX.2 default

    # Determine guidance_scale
    if request.guidance_scale is not None:
        guidance_scale = request.guidance_scale
    elif request.style is not None:
        guidance_scale = STYLE_MAPPING.get(request.style, 4.0)
    else:
        guidance_scale = 4.0  # FLUX.2 default

    return {
        "model": request.model,
        "prompt": request.prompt,
        "width": width,
        "height": height,
        "num_inference_steps": num_inference_steps,
        "guidance_scale": guidance_scale,
        "seed": request.seed,
        "response_format": request.response_format,
        "negative_prompt": request.negative_prompt,
        "caption_upsample_temperature": request.caption_upsample_temperature,
    }


async def generate_image_via_worker(
    worker_controller,
    request: ImageGenerationRequest,
    width: int,
    height: int,
) -> ImageGenerationResponse:
    """Execute image generation via worker."""
    request_id = str(uuid.uuid4())[:8]

    # Resolve parameters (explicit > quality/style mapping > defaults)
    params = resolve_generation_params(request, width, height)

    logger.info(
        f"[{request_id}] Image generation request: model={params['model']}, "
        f"prompt='{params['prompt'][:50]}...', size={width}x{height}, "
        f"steps={params['num_inference_steps']}, guidance={params['guidance_scale']}"
    )

    try:
        # Ensure model is loaded
        if not await worker_controller.ensure_model_loaded(params["model"]):
            raise HTTPException(
                status_code=503,
                detail=(
                    f"Failed to load model: {params['model']}. "
                    "Model may not exist in catalog."
                ),
            )

        # Generate image via RPC
        result = await worker_controller.generate_image(
            model_id=params["model"],
            prompt=params["prompt"],
            width=params["width"],
            height=params["height"],
            num_inference_steps=params["num_inference_steps"],
            guidance_scale=params["guidance_scale"],
            seed=params["seed"],
            response_format=params["response_format"],
            negative_prompt=params["negative_prompt"],
            caption_upsample_temperature=params["caption_upsample_temperature"],
            timeout=300.0,
        )

        logger.info(
            f"[{request_id}] Image generation complete: "
            f"{len(result.get('data', []))} image(s) generated"
        )

        # Build response with revised_prompt (Flux doesn't revise, return original)
        response_data = result.copy()
        if "data" in response_data:
            response_data["data"] = [
                {**item, "revised_prompt": item.get("revised_prompt", request.prompt)}
                if isinstance(item, dict)
                else item
                for item in response_data["data"]
            ]

        return ImageGenerationResponse(**response_data)

    except HTTPException:
        raise
    except TimeoutError:
        logger.error(f"[{request_id}] Image generation timeout after 300s")
        raise HTTPException(
            status_code=504,
            detail=(
                "Image generation timeout after 300s. "
                "Try reducing inference steps or image size."
            ),
        )
    except Exception as e:
        logger.error(f"[{request_id}] Image generation failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Image generation failed: {str(e)}",
        )
