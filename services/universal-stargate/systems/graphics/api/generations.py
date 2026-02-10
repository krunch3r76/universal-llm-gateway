"""Image generation endpoint for Stargate proxy."""

from fastapi import APIRouter, HTTPException
from model_id import ModelId
from universal_logging import get_logger

from .gateway_proxy import forward_image_request
from .schemas import ImageGenerationRequest, ImageGenerationResponse

logger = get_logger(__name__)

router = APIRouter(prefix="/images", tags=["images"])


@router.post("/generations", response_model=ImageGenerationResponse)
async def create_image(request: ImageGenerationRequest) -> ImageGenerationResponse:
    """
    Generate image using Flux.2 model (OpenAI-compatible).

    Proxies request to Gateway after ensuring model is loaded.

    **Supported Model**: flux.2-dev

    **Parameters**:
    - quality: "standard" (20 steps) or "hd" (50 steps)
    - style: "vivid" (guidance 4.0) or "natural" (guidance 2.5)
    - caption_upsample_temperature: 0.15 recommended for better prompts
    - Explicit num_inference_steps/guidance_scale override quality/style
    """
    # Lazy import to avoid circular dependency (proxy imports v1, v1 imports graphics)
    from systems.proxy.dependencies import get_proxy

    proxy = get_proxy()

    # TODO: Future Base ESCROW_LOCKED hook point
    # For token-gated access, lock escrow here before model loading

    # Parse at API boundary
    parsed_model = ModelId.parse(request.model)

    # Ensure model is loaded on a gateway
    if proxy.resource_aware_model_manager is None:
        logger.error("ResourceAwareModelManager not initialized")
        raise HTTPException(status_code=503, detail="Service not ready")

    try:
        logger.info(f"Ensuring model {parsed_model} is loaded for image generation")
        gateway_instance = await proxy.resource_aware_model_manager.ensure_model_loaded(
            parsed_model  # ModelId object
        )
        logger.info(f"Model {parsed_model} ready on {gateway_instance.config.name}")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Model loading failed: {e}")
        raise HTTPException(status_code=503, detail=f"Model loading failed: {e}")

    # Build request for Gateway (pure passthrough)
    gateway_request = request.model_dump(exclude_none=True)

    # Forward to Gateway
    gateway_url = gateway_instance.config.base_url

    try:
        response = await forward_image_request(gateway_url, gateway_request)

        # TODO: Future Base ESCROW_RELEASED hook point
        # For token-gated access, release escrow here after success

        return ImageGenerationResponse(**response)

    except HTTPException:
        # TODO: Future Base ESCROW_EXPIRED hook point
        # For token-gated access, expire/refund escrow on failure
        raise
    except Exception as e:
        logger.error(f"Image generation failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Image generation failed: {e}")
