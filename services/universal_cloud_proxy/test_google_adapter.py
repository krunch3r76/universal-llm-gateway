from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from services.universal_cloud_proxy.adapters.google import GoogleAdapter
from services.universal_cloud_proxy.config import ProviderConfig


def _make_adapter(
    handler: httpx.MockTransport | httpx.AsyncBaseTransport,
) -> GoogleAdapter:
    client = httpx.AsyncClient(transport=handler)
    config = ProviderConfig(
        provider="google",
        api_key="google-key",
        base_url="https://generativelanguage.googleapis.com/v1beta",
    )
    return GoogleAdapter(config=config, client=client)


@pytest.mark.asyncio
async def test_forward_video_generation_calls_predict_long_running() -> None:
    recorded: list[dict[str, Any]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        recorded.append(
            {
                "url": str(request.url),
                "headers": dict(request.headers),
                "body": json.loads(request.content.decode()),
            }
        )
        return httpx.Response(200, json={"name": "operations/video-123"})

    adapter = _make_adapter(httpx.MockTransport(handler))

    result = await adapter.forward_video_generation(
        {
            "model": "veo-3.1-generate-preview",
            "instances": [{"prompt": "a cinematic panning shot"}],
            "parameters": {"aspectRatio": "16:9", "durationSeconds": 8},
        }
    )

    assert recorded
    assert recorded[0]["url"].endswith(
        "/models/veo-3.1-generate-preview:predictLongRunning"
    )
    assert recorded[0]["headers"]["x-goog-api-key"] == "google-key"
    assert recorded[0]["body"] == {
        "instances": [{"prompt": "a cinematic panning shot"}],
        "parameters": {"aspectRatio": "16:9", "durationSeconds": 8},
    }
    assert result["operation_name"] == "operations/video-123"
    assert result["request_id"] == "operations%2Fvideo-123"
    assert result["status"] == "pending"


@pytest.mark.asyncio
async def test_forward_video_status_normalizes_done_response() -> None:
    recorded_urls: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        recorded_urls.append(str(request.url))
        return httpx.Response(
            200,
            json={
                "done": True,
                "response": {
                    "generateVideoResponse": {
                        "generatedSamples": [
                            {"video": {"uri": "https://example.test/video.mp4"}}
                        ]
                    }
                },
            },
        )

    adapter = _make_adapter(httpx.MockTransport(handler))

    result = await adapter.forward_video_status("operations%2Fvideo-123")

    assert recorded_urls == [
        "https://generativelanguage.googleapis.com/v1beta/operations/video-123"
    ]
    assert result["status"] == "succeeded"
    assert result["operation_name"] == "operations/video-123"
    assert result["video"] == {"uri": "https://example.test/video.mp4"}
