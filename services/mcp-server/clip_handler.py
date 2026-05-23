"""Bookmarklet clip upload handler — extracted from AuthMiddleware.

Handles POST /clip after static-token authentication is confirmed by the
caller (AuthMiddleware).  The caller is responsible for 401/405 responses;
this module only handles the POST body parsing, dedup, and file write.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from mcp_events import record
from starlette.requests import Request
from starlette.responses import JSONResponse
from tools.clip import normalize_clip_content
from universal_logging import get_logger

logger = get_logger(__name__)

CLIPS_DIR = Path("/data/files/clips")
MAX_BODY_BYTES = 5 * 1024 * 1024
CLIP_CORS_HEADERS: dict[str, str] = {
    "access-control-allow-origin": "*",
    "access-control-allow-methods": "POST, OPTIONS",
    "access-control-allow-headers": "Authorization, Content-Type",
    "access-control-max-age": "86400",
}


def _slugify(text: str, max_len: int = 60) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower())
    slug = slug.strip("-")[:max_len].rstrip("-")
    return slug or "untitled"


async def handle_clip_upload(request: Request) -> JSONResponse:
    """Process a bookmarklet clip upload after static-token authentication."""
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_BODY_BYTES:
            logger.warning("clip: rejected oversized payload (%d bytes)", len(body))
            record("mcp.clip.upload_failed", reason="payload_too_large", size=len(body))
            return JSONResponse(
                {"error": "Payload too large (5MB limit)"},
                status_code=413,
                headers=CLIP_CORS_HEADERS,
            )

    try:
        data = json.loads(body.decode("utf-8"))
    except json.JSONDecodeError:
        logger.warning("clip: rejected invalid JSON payload")
        return JSONResponse(
            {"error": "Invalid JSON"},
            status_code=400,
            headers=CLIP_CORS_HEADERS,
        )

    url = data.get("url", "").strip()
    title = data.get("title", "").strip()
    content = data.get("content", "").strip()
    selected = bool(data.get("selected", False))

    if not content:
        logger.warning("clip: rejected empty content payload")
        return JSONResponse(
            {"error": "Missing required field: content"},
            status_code=400,
            headers=CLIP_CORS_HEADERS,
        )

    content, extracted = normalize_clip_content(content)
    if not title:
        title = "Untitled Clip"

    ts = int(time.time())
    slug = _slugify(title)
    filename = f"{slug}-{ts}.md"
    CLIPS_DIR.mkdir(parents=True, exist_ok=True)

    title_sanitized = title.replace("\r", "").replace("\n", " ")
    url_sanitized = url.replace("\r", "").replace("\n", " ")
    safe_title = title_sanitized.replace("\\", "\\\\").replace('"', '\\"')
    safe_url = url_sanitized.replace("\\", "\\\\").replace('"', '\\"')
    frontmatter = f"""
---
url: "{safe_url}"
title: "{safe_title}"
clipped_at: {ts}
selected: {str(selected).lower()}
extracted: {str(extracted).lower()}
chars: {len(content)}
---

"""

    for attempt in range(5):
        candidate = CLIPS_DIR / (f"{slug}-{ts + attempt}.md" if attempt else filename)
        try:
            with candidate.open("x", encoding="utf-8") as clip_file:
                clip_file.write(frontmatter + content)
            filename = candidate.name
            break
        except FileExistsError:
            logger.debug("clip: filename '%s' already exists, retrying", candidate)
            continue
        except OSError as exc:
            logger.error("clip: failed writing %s: %s", candidate, exc)
            return JSONResponse(
                {"error": "Failed to save clip"},
                status_code=500,
                headers=CLIP_CORS_HEADERS,
            )
    else:
        logger.error("clip: unable to allocate unique filename for slug '%s'", slug)
        return JSONResponse(
            {"error": f"Unable to allocate unique clip filename for slug '{slug}'"},
            status_code=409,
            headers=CLIP_CORS_HEADERS,
        )

    logger.info(
        "clip: saved %s (%d chars, selected=%s)", filename, len(content), selected
    )
    return JSONResponse(
        {"status": "clipped", "clip_id": filename},
        headers=CLIP_CORS_HEADERS,
    )
