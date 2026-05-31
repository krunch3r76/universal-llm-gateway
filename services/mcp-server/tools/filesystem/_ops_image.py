"""Image viewing operation implementation."""

from __future__ import annotations

import base64
import logging
from typing import Literal

from mcp.types import ImageContent
from mcp_events import record

from ._image_render import (
    render_thumbnail_bytes,
    shared_image_name,
    write_shared_image,
)
from ._paths import ALLOWED_IMAGE_SUFFIXES, normalize_files_reference, safe_path

logger = logging.getLogger(__name__)


def view_image_impl(
    path: str,
    max_dimension: int = 1024,
    quality: int = 60,
    mode: Literal["copy", "image"] = "copy",
) -> ImageContent | dict[str, str | int]:
    """View a photo or image from the sandbox filesystem.

    Resizes to a JPEG thumbnail. Prefer ``copy`` (default) when the client
    can open local files without bloating the MCP payload. Use ``image`` only
    when the response itself must carry inline pixels.
    """
    normalized_path = normalize_files_reference(path)
    src = safe_path(normalized_path)
    if not src.exists():
        raise FileNotFoundError(f"Image not found: {path!r}")
    if not src.is_file():
        raise ValueError(f"Path is not a file: {path!r}")

    suffix = src.suffix.lower()
    if suffix not in ALLOWED_IMAGE_SUFFIXES:
        raise ValueError(
            f"Unsupported image format {suffix!r}. "
            f"Allowed: {', '.join(sorted(ALLOWED_IMAGE_SUFFIXES))}"
        )

    jpeg_bytes, original_size, thumb_size = render_thumbnail_bytes(
        src,
        max_dimension=max_dimension,
        quality=quality,
    )

    record(
        "mcp.tool.image.viewed",
        path=normalized_path,
        resolved=str(src),
        original=original_size,
        thumbnail_size=thumb_size,
        bytes=len(jpeg_bytes),
        mode=mode,
    )
    logger.info(
        "view_image: %s %s -> %s (%d bytes, mode=%s)",
        src,
        original_size,
        thumb_size,
        len(jpeg_bytes),
        mode,
    )

    if mode == "image":
        return ImageContent(
            type="image",
            data=base64.b64encode(jpeg_bytes).decode(),
            mimeType="image/jpeg",
        )

    shared_name = shared_image_name(
        src,
        max_dimension=max_dimension,
        quality=quality,
    )
    shared_path, shared_host_path = write_shared_image(shared_name, jpeg_bytes)
    logger.info("view_image copy: %s", shared_path)
    return {
        "path": str(shared_host_path),
        "dimensions": thumb_size,
        "original": original_size,
        "bytes": len(jpeg_bytes),
    }
