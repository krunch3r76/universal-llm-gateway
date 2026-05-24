"""Execution helpers for image and video generation via Stargate provider-native routes.

Image generation is synchronous (single HTTP round-trip).
Video generation is asynchronous: POST returns a request_id, then we poll
GET until status == "done" or the timeout is exceeded.
"""

from __future__ import annotations

import os
import time
from typing import Any

import httpx
from mcp_events import monotonic_now, record
from universal_logging import get_logger

logger = get_logger(__name__)

STARGATE_URL = os.environ.get("STARGATE_URL", "http://io:9999")
_IMAGE_TIMEOUT = httpx.Timeout(connect=10.0, read=120.0, write=30.0, pool=10.0)
_VIDEO_SUBMIT_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=30.0, pool=10.0)
_VIDEO_POLL_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0)
_VIDEO_POLL_INTERVAL = 4.0
_VIDEO_POLL_DONE_STATUSES = {"succeeded", "done", "completed"}
_VIDEO_POLL_FAIL_STATUSES = {"failed", "cancelled", "error"}


def execute_frontier_image(
    *,
    provider: str,
    endpoint: str,
    body: dict[str, Any],
    timeout: float | None = None,
) -> dict[str, Any]:
    """POST to Stargate /api/v1/providers/{provider}/images/{endpoint}.

    ``endpoint`` is ``"generations"`` or ``"edits"``.
    Returns the parsed JSON response, or an error dict on failure.
    """
    path = f"/api/v1/providers/{provider}/images/{endpoint}"
    t0 = monotonic_now()
    model = str(body.get("model", ""))
    record(
        "mcp.imagine.image.called",
        provider=provider,
        endpoint=endpoint,
        model=model,
    )

    effective_timeout = _IMAGE_TIMEOUT
    if timeout is not None:
        clamped = min(max(timeout, 10.0), 300.0)
        effective_timeout = httpx.Timeout(
            connect=10.0, read=clamped, write=30.0, pool=10.0
        )

    try:
        with httpx.Client(timeout=effective_timeout) as http:
            resp = http.post(
                f"{STARGATE_URL}{path}",
                json=body,
                headers={"Content-Type": "application/json"},
            )
        if resp.status_code >= 400:
            record(
                "mcp.imagine.image.error",
                provider=provider,
                endpoint=endpoint,
                error=f"upstream_{resp.status_code}",
            )
            return {
                "error": f"Upstream error ({resp.status_code})",
                "detail": resp.text[:500],
            }

        raw = resp.json()
        duration = monotonic_now() - t0
        record(
            "mcp.imagine.image.completed",
            provider=provider,
            endpoint=endpoint,
            model=model,
            duration_s=round(duration, 3),
            n=len(raw.get("data", [])),
        )
        return raw

    except httpx.TimeoutException:
        duration = monotonic_now() - t0
        record(
            "mcp.imagine.image.error",
            provider=provider,
            endpoint=endpoint,
            error="timeout",
            duration_s=round(duration, 3),
        )
        return {"error": f"Request timed out after {int(duration)}s"}
    except httpx.RequestError as exc:
        logger.error("Image generation upstream failed: %s", exc)
        record(
            "mcp.imagine.image.error",
            provider=provider,
            endpoint=endpoint,
            error="connection",
        )
        return {"error": "Upstream connection failed"}


def execute_frontier_video(
    *,
    provider: str,
    body: dict[str, Any],
    poll_timeout: float = 120.0,
) -> dict[str, Any]:
    """Submit video generation job then poll until done or timeout.

    Returns the completed video response (with ``video.url``) or an error dict.
    Polling happens synchronously — the MCP tool will block until the video
    is ready (or the timeout expires).
    """
    submit_path = f"/api/v1/providers/{provider}/videos/generations"
    model = str(body.get("model", ""))
    t0 = monotonic_now()
    record("mcp.imagine.video.called", provider=provider, model=model)

    try:
        with httpx.Client(timeout=_VIDEO_SUBMIT_TIMEOUT) as http:
            resp = http.post(
                f"{STARGATE_URL}{submit_path}",
                json=body,
                headers={"Content-Type": "application/json"},
            )
        if resp.status_code >= 400:
            record(
                "mcp.imagine.video.error",
                provider=provider,
                error=f"submit_{resp.status_code}",
                model=model,
            )
            return {
                "error": f"Video submit error ({resp.status_code})",
                "detail": resp.text[:500],
            }

        submit_result = resp.json()
        request_id = (
            submit_result.get("id")
            or submit_result.get("request_id")
            or submit_result.get("name")
        )
        if not request_id:
            record(
                "mcp.imagine.video.error",
                provider=provider,
                error="no_request_id",
                model=model,
            )
            return {
                "error": "No request_id in video generation response",
                "raw": submit_result,
            }

        record(
            "mcp.imagine.video.submitted",
            provider=provider,
            model=model,
            request_id=request_id,
        )

        # Poll until done
        deadline = time.monotonic() + poll_timeout
        with httpx.Client(timeout=_VIDEO_POLL_TIMEOUT) as http:
            while time.monotonic() < deadline:
                time.sleep(_VIDEO_POLL_INTERVAL)
                poll_resp = http.get(
                    f"{STARGATE_URL}/api/v1/providers/{provider}/videos/{request_id}",
                )
                if poll_resp.status_code >= 400:
                    logger.warning(
                        "Video poll %s/%s returned %d",
                        provider,
                        request_id,
                        poll_resp.status_code,
                    )
                    continue

                poll_result = poll_resp.json()
                status = str(poll_result.get("status", "")).lower()
                is_google_done = poll_result.get("done") is True

                if status in _VIDEO_POLL_DONE_STATUSES or is_google_done:
                    if isinstance(poll_result.get("error"), dict):
                        record(
                            "mcp.imagine.video.error",
                            provider=provider,
                            error="generation_error",
                            model=model,
                            request_id=request_id,
                        )
                        return {
                            "error": "Video generation failed",
                            "request_id": request_id,
                            "detail": poll_result,
                        }
                    duration = monotonic_now() - t0
                    record(
                        "mcp.imagine.video.completed",
                        provider=provider,
                        model=model,
                        request_id=request_id,
                        duration_s=round(duration, 3),
                    )
                    return poll_result

                if status in _VIDEO_POLL_FAIL_STATUSES:
                    record(
                        "mcp.imagine.video.error",
                        provider=provider,
                        error=f"generation_{status}",
                        model=model,
                        request_id=request_id,
                    )
                    return {
                        "error": f"Video generation {status}",
                        "request_id": request_id,
                        "detail": poll_result,
                    }

        duration = monotonic_now() - t0
        record(
            "mcp.imagine.video.error",
            provider=provider,
            error="timeout",
            model=model,
            request_id=request_id,
            duration_s=round(duration, 3),
        )
        return {
            "error": f"Video generation timed out after {int(poll_timeout)}s",
            "request_id": request_id,
        }

    except httpx.TimeoutException:
        duration = monotonic_now() - t0
        record(
            "mcp.imagine.video.error",
            provider=provider,
            error="submit_timeout",
            model=model,
        )
        return {"error": f"Video submit timed out after {int(duration)}s"}
    except httpx.RequestError as exc:
        logger.error("Video generation upstream failed: %s", exc)
        record(
            "mcp.imagine.video.error",
            provider=provider,
            error="connection",
            model=model,
        )
        return {"error": "Upstream connection failed"}
