"""Image and video generation MCP tools — grok_imagine and openai_imagine.

Both tools are thin wrappers over provider-native image generation APIs routed
through Stargate's /api/v1/providers/{provider}/images/* and /videos/* surfaces.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from model_id import ModelId

from ._frontier_imagine import execute_frontier_image, execute_frontier_video

if TYPE_CHECKING:
    from fastmcp import FastMCP

# xAI image models available via /v1/images/generations or /v1/images/edits
_XAI_IMAGE_MODELS = frozenset({
    "grok-imagine-image",
    "grok-imagine-image-pro",
})

# xAI video model — uses async submit + poll
_XAI_VIDEO_MODELS = frozenset({
    "grok-imagine-video",
})

# OpenAI image models
_OPENAI_IMAGE_MODELS = frozenset({
    "gpt-image-1",
    "gpt-image-1-mini",
    "gpt-image-1.5",
    "dall-e-3",
    "dall-e-2",
    "chatgpt-image-latest",
})


def register_imagine_tools(mcp: FastMCP) -> None:
    """Register grok_imagine and openai_imagine."""

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
        include_raw: bool = False,
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

        is_video = base_model in _XAI_VIDEO_MODELS or "video" in base_model

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
            return execute_frontier_video(body=body, poll_timeout=poll_timeout)

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
            include_raw=include_raw,
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
        include_raw: bool = False,
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
            include_raw=include_raw,
            timeout=timeout,
        )
