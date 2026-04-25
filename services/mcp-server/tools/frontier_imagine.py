"""Image and video generation MCP tools — grok_imagine, openai_imagine, and google_imagine.

These tools are thin wrappers over provider-native image generation APIs routed
through Stargate's /api/v1/providers/{provider}/images/* and /videos/* surfaces.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from model_id import ModelId

from ._frontier_imagine import execute_frontier_image, execute_frontier_video

if TYPE_CHECKING:
    from fastmcp import FastMCP

# xAI image models available via /v1/images/generations or /v1/images/edits
_XAI_IMAGE_MODELS = frozenset(
    {
        "grok-imagine-image",
        "grok-imagine-image-pro",
    }
)

# xAI video model — uses async submit + poll
_XAI_VIDEO_MODELS = frozenset(
    {
        "grok-imagine-video",
    }
)

# Google Veo video models — uses async submit + poll
_GOOGLE_VIDEO_MODELS = frozenset(
    {
        "veo-2.0-generate-001",
        "veo-3.0-generate-001",
        "veo-3.0-fast-generate-001",
        "veo-3.1-generate-preview",
        "veo-3.1-fast-generate-preview",
    }
)

# OpenAI image models
_OPENAI_IMAGE_MODELS = frozenset(
    {
        "gpt-image-1",
        "gpt-image-1-mini",
        "gpt-image-1.5",
        "dall-e-3",
        "dall-e-2",
        "chatgpt-image-latest",
    }
)


def _inline_image(image_base64: str, mime_type: str) -> dict[str, Any]:
    return {"inlineData": {"mimeType": mime_type, "data": image_base64}}


def register_imagine_tools(mcp: FastMCP) -> None:
    """Register grok_imagine, openai_imagine, and google_imagine."""

    @mcp.tool(title="Grok Imagine")
    def grok_imagine(
        prompt: str,
        model: str = "grok-imagine-image",
        n: int | None = None,
        response_format: str | None = None,
        aspect_ratio: str | None = None,
        resolution: str | None = None,
        image_url: str | None = None,
        duration: int | None = None,
        poll_timeout: float = 180.0,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Generate images or videos with xAI Grok Imagine models.

        **Image models** (synchronous):
          grok-imagine-image      — photorealistic, style-transfer, editing (DEFAULT)
          grok-imagine-image-pro  — higher quality / resolution tier

        **Video model** (async, polls until done):
          grok-imagine-video      — text-to-video or image-to-video, up to 15s

        **Image generation** (default, no ``image_url``):
          POST /v1/images/generations — returns data[].url or data[].b64_json

        **Image editing** (provide ``image_url``):
          POST /v1/images/edits — edits an existing image based on the prompt.
          Provide the source as a public URL or ``data:image/png;base64,...``.
          xAI uses application/json (not multipart/form-data) for edits.

        **Video generation** (model="grok-imagine-video"):
          Submits a job then polls every 4s until done (or ``poll_timeout`` expires).
          Returns the completed response with ``video.url``. Typical wait: 30–90s.
          Optional ``image_url`` produces image-to-video output.

        **Key parameters**:
        - ``n``: number of images (max 10). Ignored for video.
        - ``response_format``: ``"url"`` (default, temporary) or ``"b64_json"``.
        - ``aspect_ratio``: ``"16:9"``, ``"1:1"``, ``"9:16"``, ``"4:3"``, etc.
          For video: also ``"9:19.5"``, ``"20:9"``.
          For single-image edit: output follows input image's ratio (override with aspect_ratio).
        - ``resolution``: ``"1k"`` or ``"2k"`` for images; ``"720p"`` or ``"480p"`` for video.
        - ``duration``: seconds of video (1–15, video only).
        - ``poll_timeout``: max seconds to wait for video completion (default 180).
        - ``timeout``: override image request read timeout (seconds, max 300).
        """
        base_model = model.split("/")[-1] if "/" in model else model
        full_model = model if "/" in model else f"xai/{model}"
        api_model = ModelId.parse(full_model).api_model_id

        is_video = base_model in _XAI_VIDEO_MODELS

        if is_video:
            body: dict[str, Any] = {"model": api_model, "prompt": prompt}
            if duration is not None:
                body["duration"] = duration
            if aspect_ratio is not None:
                body["aspect_ratio"] = aspect_ratio
            if resolution is not None:
                body["resolution"] = resolution
            if image_url is not None:
                body["image"] = {"url": image_url}
            return execute_frontier_video(
                provider="xai",
                body=body,
                poll_timeout=poll_timeout,
            )

        # Image generation or editing
        endpoint = "edits" if image_url else "generations"
        body = {"model": api_model, "prompt": prompt}
        if n is not None:
            body["n"] = n
        if response_format is not None:
            body["response_format"] = response_format
        if aspect_ratio is not None:
            body["aspect_ratio"] = aspect_ratio
        if resolution is not None:
            body["resolution"] = resolution
        if image_url is not None:
            body["image"] = {"url": image_url}

        return execute_frontier_image(
            provider="xai",
            endpoint=endpoint,
            body=body,
            timeout=timeout,
        )

    @mcp.tool(title="OpenAI Imagine")
    def openai_imagine(
        prompt: str,
        model: str = "gpt-image-1",
        n: int | None = None,
        size: str | None = None,
        quality: str | None = None,
        style: str | None = None,
        response_format: str | None = None,
        image_url: str | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Generate or edit images with OpenAI image models.

        **Models** (generation):
          gpt-image-1         — latest flagship image model (DEFAULT)
          gpt-image-1-mini    — cheaper, faster tier
          gpt-image-1.5       — improved gpt-image-1
          chatgpt-image-latest — alias for the current best ChatGPT image model
          dall-e-3            — DALL·E 3 (vivid/natural style, HD quality)
          dall-e-2            — DALL·E 2 (legacy, lower cost)

        **Image editing** (provide ``image_url``):
          Edit an existing image. Not all models support editing — gpt-image-1
          and dall-e-2 support edits; dall-e-3 does not.

        **Key parameters**:
        - ``n``: images to generate (1–10; dall-e-3 supports only 1).
        - ``size``: ``"1024x1024"``, ``"1792x1024"``, ``"1024x1792"`` (dall-e-3);
          ``"256x256"``, ``"512x512"``, ``"1024x1024"`` (dall-e-2);
          ``"1024x1024"``, ``"1536x1024"``, ``"1024x1536"``, ``"auto"`` (gpt-image-1).
        - ``quality``: ``"standard"`` or ``"hd"`` (dall-e-3);
          ``"low"``, ``"medium"``, ``"high"``, ``"auto"`` (gpt-image-1).
        - ``style``: ``"vivid"`` or ``"natural"`` (dall-e-3 only).
        - ``response_format``: ``"url"`` (default) or ``"b64_json"``.
        - ``image_url``: source image URL or base64 data URI for edits.
        """
        full_model = model if "/" in model else f"openai/{model}"
        api_model = ModelId.parse(full_model).api_model_id

        endpoint = "edits" if image_url else "generations"
        body: dict[str, Any] = {"model": api_model, "prompt": prompt}
        if n is not None:
            body["n"] = n
        if size is not None:
            body["size"] = size
        if quality is not None:
            body["quality"] = quality
        if style is not None:
            body["style"] = style
        if response_format is not None:
            body["response_format"] = response_format
        if image_url is not None:
            body["image"] = {"url": image_url}

        return execute_frontier_image(
            provider="openai",
            endpoint=endpoint,
            body=body,
            timeout=timeout,
        )

    @mcp.tool(title="Google Imagine")
    def google_imagine(
        prompt: str,
        model: str = "veo-3.1-generate-preview",
        aspect_ratio: str | None = None,
        resolution: str | None = None,
        duration: int | None = None,
        person_generation: str | None = None,
        seed: int | None = None,
        image: dict[str, Any] | None = None,
        image_base64: str | None = None,
        image_mime_type: str = "image/png",
        last_frame: dict[str, Any] | None = None,
        last_frame_base64: str | None = None,
        reference_images: list[dict[str, Any]] | None = None,
        poll_timeout: float = 240.0,
    ) -> dict[str, Any]:
        """Generate videos with Google Veo models.

        **Models**:
          veo-3.1-generate-preview       — flagship video model with native audio (DEFAULT)
          veo-3.1-fast-generate-preview  — faster video model with native audio
          veo-3.0-generate-001           — stable Veo 3
          veo-3.0-fast-generate-001      — stable fast Veo 3
          veo-2.0-generate-001           — Veo 2, silent video

        **Text-to-video**:
          Provide ``prompt`` only.

        **Image-to-video / interpolation**:
          Provide ``image`` as a Gemini Image object, or ``image_base64`` plus
          ``image_mime_type``. Provide ``last_frame`` or ``last_frame_base64``
          with ``image`` for first-to-last-frame transitions.

        **Reference images**:
          For Veo 3.1, pass ``reference_images`` in Gemini REST shape:
          ``[{"image": {"inlineData": {...}}, "referenceType": "asset"}]``.

        **Key parameters**:
        - ``aspect_ratio`` maps to Google ``aspectRatio`` (``"16:9"`` or ``"9:16"``).
        - ``duration`` maps to ``durationSeconds`` (typically 4, 6, or 8).
        - ``resolution`` maps to ``"720p"``, ``"1080p"``, or ``"4k"``.
        - ``person_generation`` maps to ``personGeneration``.
        """
        base_model = model.split("/")[-1] if "/" in model else model
        full_model = model if "/" in model else f"google/{model}"
        api_model = ModelId.parse(full_model).api_model_id

        if base_model not in _GOOGLE_VIDEO_MODELS and not base_model.startswith("veo-"):
            return {"error": f"Unsupported Google video model: {model}"}

        instance: dict[str, Any] = {"prompt": prompt}
        if image is not None:
            instance["image"] = image
        elif image_base64 is not None:
            instance["image"] = _inline_image(image_base64, image_mime_type)
        if last_frame is not None:
            instance["lastFrame"] = last_frame
        elif last_frame_base64 is not None:
            instance["lastFrame"] = _inline_image(last_frame_base64, image_mime_type)
        if reference_images is not None:
            instance["referenceImages"] = reference_images

        parameters: dict[str, Any] = {}
        if aspect_ratio is not None:
            parameters["aspectRatio"] = aspect_ratio
        if resolution is not None:
            parameters["resolution"] = resolution
        if duration is not None:
            parameters["durationSeconds"] = duration
        if person_generation is not None:
            parameters["personGeneration"] = person_generation
        if seed is not None:
            parameters["seed"] = seed

        body: dict[str, Any] = {"model": api_model, "instances": [instance]}
        if parameters:
            body["parameters"] = parameters

        return execute_frontier_video(
            provider="google",
            body=body,
            poll_timeout=poll_timeout,
        )
