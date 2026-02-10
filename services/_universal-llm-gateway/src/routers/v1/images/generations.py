"""OpenAI-compatible image generation endpoint for Flux.2."""

from fastapi import APIRouter, Depends

from src.routers.dependencies import get_worker_controller

from .schemas import ImageGenerationRequest, ImageGenerationResponse
from .validation import parse_and_validate_size
from .worker_ops import generate_image_via_worker

router = APIRouter()


@router.post("/images/generations", response_model=ImageGenerationResponse)
async def create_image(
    request: ImageGenerationRequest,
    worker_controller=Depends(get_worker_controller),
):
    """
    Generate image using Flux.2 model (OpenAI-compatible).

    Creates an image from a text prompt using the specified Flux.2 model.
    Compatible with OpenAI's `/v1/images/generations` API.

    **Supported Model:** flux.2-dev

    **FLUX.2 Features:**
    - Caption upsampling for improved prompt adherence
      (set caption_upsample_temperature=0.15)
    - Efficient convergence (20 steps for standard, 50 for HD)
    - Better quality at lower guidance values
    """
    # Parse and validate size
    width, height = parse_and_validate_size(request.size)

    # Execute generation via worker
    return await generate_image_via_worker(worker_controller, request, width, height)
