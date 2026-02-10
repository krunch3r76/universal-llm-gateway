"""Request/response schemas for image generation proxy."""

from typing import Literal

from pydantic import BaseModel, Field


class ImageGenerationRequest(BaseModel):
    """OpenAI-compatible image generation request."""

    model: str = Field(..., description="Model ID (e.g., flux.2-dev)")
    prompt: str = Field(..., description="Text prompt for image generation")
    n: int = Field(1, ge=1, le=1, description="Number of images")
    size: str = Field("1024x1024", description="Image size")
    response_format: Literal["url", "b64_json"] = Field("b64_json")

    # OpenAI-compatible params
    quality: Literal["standard", "hd"] | None = None
    style: Literal["vivid", "natural"] | None = None
    negative_prompt: str | None = None

    # Flux.2-specific overrides
    num_inference_steps: int | None = Field(None, ge=1, le=150)
    guidance_scale: float | None = Field(None, ge=0.0, le=20.0)
    seed: int | None = None
    caption_upsample_temperature: float | None = Field(None, ge=0.0, le=1.0)


class ImageData(BaseModel):
    """Image data in response."""

    url: str
    revised_prompt: str | None = None


class ImageGenerationResponse(BaseModel):
    """OpenAI-compatible image generation response."""

    created: int
    data: list[ImageData]
