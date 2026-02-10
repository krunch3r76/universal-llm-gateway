"""Parameter extraction and validation for FLUX.2 generation."""

from dataclasses import dataclass


@dataclass(slots=True, kw_only=True)
class GenerationParams:
    """FLUX.2 generation parameters."""

    prompt: str
    negative_prompt: str | None
    width: int
    height: int
    num_inference_steps: int
    guidance_scale: float
    caption_upsample_temperature: float | None
    seed: int | None
    response_format: str


def extract_generation_params(data: dict) -> GenerationParams:
    """
    Extract and validate generation parameters from request data.

    Args:
        data: Request data dictionary

    Returns:
        Validated GenerationParams

    Raises:
        ValueError: If required parameters are missing or invalid
    """
    prompt = data.get("prompt")
    if not prompt:
        raise ValueError("prompt is required")

    return GenerationParams(
        prompt=prompt,
        negative_prompt=data.get("negative_prompt"),
        width=data.get("width", 1024),
        height=data.get("height", 1024),
        num_inference_steps=data.get("num_inference_steps", 20),
        guidance_scale=data.get("guidance_scale", 4.0),
        caption_upsample_temperature=data.get("caption_upsample_temperature"),
        seed=data.get("seed"),
        response_format=data.get("response_format", "b64_json"),
    )
