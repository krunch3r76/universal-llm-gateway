"""Image generation request/response schemas."""

from typing import Literal

from pydantic import BaseModel, Field


class ImageGenerationRequest(BaseModel):
    """OpenAI-compatible image generation request."""

    model: str = Field(..., description="Model ID (e.g., flux.2-dev)")
    prompt: str = Field(..., description="Text prompt for image generation")
    n: int = Field(
        1, ge=1, le=1, description="Number of images (currently only 1 supported)"
    )
    size: str = Field("1024x1024", description="Image size (e.g., 1024x1024, 512x512)")
    response_format: Literal["url", "b64_json"] = Field(
        "b64_json", description="Response format"
    )

    # OpenAI-compatible params (mapped to Flux.2 params via config)
    quality: Literal["standard", "hd"] | None = Field(
        None, description="Quality preset (standard=20 steps, hd=50 steps)"
    )
    style: Literal["vivid", "natural"] | None = Field(
        None, description="Style preset (vivid=4.0 guidance, natural=2.5 guidance)"
    )
    negative_prompt: str | None = Field(
        None, description="Negative prompt (what to avoid)"
    )

    # Flux.2-specific overrides (take precedence over quality/style)
    num_inference_steps: int | None = Field(
        None, ge=1, le=150, description="Number of denoising steps (overrides quality)"
    )
    guidance_scale: float | None = Field(
        None, ge=0.0, le=20.0, description="Guidance strength (overrides style)"
    )
    seed: int | None = Field(None, description="Random seed for reproducibility")
    caption_upsample_temperature: float | None = Field(
        None,
        ge=0.0,
        le=1.0,
        description="Caption upsampling temperature (0.15 recommended for FLUX.2)",
    )


class ImageData(BaseModel):
    """Image data in response."""

    url: str = Field(..., description="Base64 data URL or HTTP URL")
    revised_prompt: str | None = Field(
        None, description="The prompt used for generation (may differ from input)"
    )


class ImageGenerationResponse(BaseModel):
    """OpenAI-compatible image generation response."""

    created: int = Field(..., description="Unix timestamp")
    data: list[ImageData] = Field(..., description="List of generated images")
