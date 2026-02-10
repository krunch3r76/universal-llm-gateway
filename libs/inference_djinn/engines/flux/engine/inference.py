"""Diffusion pipeline execution for FLUX.2."""

import asyncio

from universal_logging import get_logger

from .params import GenerationParams

logger = get_logger(__name__)


async def run_diffusion_pipeline(pipe, params: GenerationParams, device: str):
    """
    Execute FLUX.2 diffusion pipeline for image generation.

    Args:
        pipe: Loaded Flux2Pipeline
        params: Generation parameters
        device: Device string ("cuda" or "cpu")

    Returns:
        PIL Image object

    Raises:
        Exception: If generation fails
    """
    import torch

    # Set seed if provided
    generator = None
    if params.seed is not None:
        generator = torch.Generator(device=device).manual_seed(params.seed)

    logger.info(
        f"Generating image: prompt='{params.prompt[:50]}...', "
        f"steps={params.num_inference_steps}, guidance={params.guidance_scale}, "
        f"size={params.width}x{params.height}, seed={params.seed}"
    )

    # Build pipeline kwargs
    pipe_kwargs = {
        "prompt": params.prompt,
        "num_inference_steps": params.num_inference_steps,
        "guidance_scale": params.guidance_scale,
        "generator": generator,
        "width": params.width,
        "height": params.height,
    }

    # Include negative_prompt if provided
    if params.negative_prompt:
        pipe_kwargs["negative_prompt"] = params.negative_prompt

    # Include caption_upsample_temperature if provided (FLUX.2 feature)
    if params.caption_upsample_temperature is not None:
        pipe_kwargs["caption_upsample_temperature"] = (
            params.caption_upsample_temperature
        )

    # Run generation in executor to avoid blocking event loop
    loop = asyncio.get_running_loop()
    image = await loop.run_in_executor(
        None,
        lambda: pipe(**pipe_kwargs).images[0],
    )

    return image
