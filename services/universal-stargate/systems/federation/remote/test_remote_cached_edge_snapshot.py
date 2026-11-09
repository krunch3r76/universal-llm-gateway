"""Unit tests for Relay cached Edge GATEWAY_SNAPSHOT write-through."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from systems.federation.common.config import StargateMode
from systems.federation.common.config.schema import FederationConfig, LocalEdgeConfig
from systems.federation.link.ws.remote.client import RemoteWebSocketClient
from systems.federation.remote.integration import RemoteIntegration

QWEN_14B = "qwen3-14b-q4-k-m-40960"
QWEN_59B = "qwen3-5-9b-q8-0-131072"
HERMES = "hermes-3-llama-3-1-70b-uncensored-q4-k-m-32768"


@pytest.fixture
def relay_integration() -> RemoteIntegration:
    config = FederationConfig(
        mode=StargateMode.REMOTE,
        stargate_id="relay-jupiter",
        local_edge=LocalEdgeConfig(
            socket_path="/tmp/edge.sock",
            stargate_id="edge-jupiter",
            api_key="test-key",
        ),
    )
    return RemoteIntegration(config)


@pytest.mark.asyncio
async def test_cached_snapshot_write_through_and_resend(
    relay_integration: RemoteIntegration,
):
    """Lifecycle + resource telemetry updates cache used on Master reconnect resend."""
    peer_id = "edge-jupiter"
    snapshot_data = {
        "gateway_id": "gw-jupiter",
        "loaded_models": [QWEN_14B, QWEN_59B],
        "busy_models": [],
        "loading_models": [],
        "available_vram_mb": 8000,
    }

    with patch("systems.federation.remote.api.telemetry.ingest_edge_telemetry"):
        await relay_integration._forward_edge_telemetry(
            peer_id, "telemetry.gateway.snapshot", snapshot_data
        )
        snapshot_data["loaded_models"].append("aliasing-probe")

        for model_id in (QWEN_14B, QWEN_59B):
            await relay_integration._forward_edge_telemetry(
                peer_id,
                "telemetry.model.unloaded",
                {"model_id": model_id},
            )
        await relay_integration._forward_edge_telemetry(
            peer_id,
            "telemetry.model.loaded",
            {"model_id": HERMES},
        )
        await relay_integration._forward_edge_telemetry(
            peer_id,
            "telemetry.resource.updated",
            {"available_vram_mb": 1300},
        )

    cached = relay_integration._cached_edge_snapshot
    assert cached is not None
    assert cached["data"]["loaded_models"] == [HERMES]
    assert cached["data"]["busy_models"] == []
    assert cached["data"]["available_vram_mb"] == 1300
    assert "aliasing-probe" not in cached["data"]["loaded_models"]

    forward_mock = AsyncMock()
    telemetry_sender = MagicMock()
    telemetry_sender.forward_edge_telemetry = forward_mock
    remote_client = MagicMock(spec=RemoteWebSocketClient)
    remote_client.telemetry_sender = telemetry_sender
    relay_integration._connection_manager = MagicMock(remote_client=remote_client)

    await relay_integration._resend_telemetry_after_reconnect()
    await asyncio.sleep(0)

    forward_mock.assert_called_once()
    call_args = forward_mock.call_args[0]
    assert call_args[0] == "edge-jupiter"
    assert call_args[1] == "telemetry.gateway.snapshot"
    resent = call_args[2]
    assert resent["loaded_models"] == [HERMES]
    assert resent["busy_models"] == []
    assert resent["available_vram_mb"] == 1300


@pytest.mark.asyncio
async def test_on_edge_disconnected_clears_cached_model_lists(
    relay_integration: RemoteIntegration,
):
    """Edge disconnect empties model lists in cached snapshot for Master resend."""
    relay_integration._cached_edge_snapshot = {
        "msg_type": "telemetry.gateway.snapshot",
        "data": {
            "loaded_models": [QWEN_14B],
            "busy_models": [QWEN_14B],
            "loading_models": [QWEN_59B],
        },
    }

    with (
        patch(
            "systems.federation.remote.api.telemetry.handle_edge_disconnected",
            return_value=[QWEN_14B],
        ),
        patch.object(
            relay_integration,
            "_forward_edge_telemetry",
            new_callable=AsyncMock,
        ),
    ):
        await relay_integration._on_edge_disconnected()

    snap_data = relay_integration._cached_edge_snapshot["data"]
    assert snap_data["loaded_models"] == []
    assert snap_data["busy_models"] == []
    assert snap_data["loading_models"] == []


def _wire_forward_mock(relay_integration: RemoteIntegration) -> AsyncMock:
    forward_mock = AsyncMock()
    telemetry_sender = MagicMock()
    telemetry_sender.forward_edge_telemetry = forward_mock
    remote_client = MagicMock(spec=RemoteWebSocketClient)
    remote_client.telemetry_sender = telemetry_sender
    relay_integration._connection_manager = MagicMock(remote_client=remote_client)
    return forward_mock


@pytest.mark.asyncio
async def test_resource_update_does_not_overwrite_model_lists(
    relay_integration: RemoteIntegration,
):
    """Stale loaded/busy in RESOURCE_UPDATE must not clobber lifecycle cache."""
    relay_integration._cached_edge_snapshot = {
        "msg_type": "telemetry.gateway.snapshot",
        "data": {
            "loaded_models": [HERMES],
            "busy_models": [HERMES],
            "loading_models": [],
            "available_vram_mb": 2000,
        },
    }

    with patch("systems.federation.remote.api.telemetry.ingest_edge_telemetry"):
        await relay_integration._forward_edge_telemetry(
            "edge-jupiter",
            "telemetry.resource.updated",
            {
                "available_vram_mb": 1500,
                "loaded_models": [QWEN_14B, QWEN_59B],
                "busy_models": [QWEN_14B],
            },
        )

    snap = relay_integration._cached_edge_snapshot["data"]
    assert snap["loaded_models"] == [HERMES]
    assert snap["busy_models"] == [HERMES]
    assert snap["available_vram_mb"] == 1500


@pytest.mark.asyncio
async def test_model_lifecycle_transitions_update_cached_snapshot(
    relay_integration: RemoteIntegration,
):
    """Busy, idle, loading.started, and loading.failed update the resend cache."""
    relay_integration._cached_edge_snapshot = {
        "msg_type": "telemetry.gateway.snapshot",
        "data": {
            "loaded_models": [HERMES],
            "busy_models": [],
            "loading_models": [],
        },
    }
    peer_id = "edge-jupiter"

    with patch("systems.federation.remote.api.telemetry.ingest_edge_telemetry"):
        await relay_integration._forward_edge_telemetry(
            peer_id,
            "telemetry.model.busy",
            {"model_id": HERMES},
        )
        await relay_integration._forward_edge_telemetry(
            peer_id,
            "telemetry.model.idle",
            {"model_id": HERMES},
        )
        await relay_integration._forward_edge_telemetry(
            peer_id,
            "telemetry.model.loading.started",
            {"model_id": QWEN_14B},
        )
        await relay_integration._forward_edge_telemetry(
            peer_id,
            "telemetry.model.loading.failed",
            {"model_id": QWEN_14B},
        )

    snap = relay_integration._cached_edge_snapshot["data"]
    assert snap["loaded_models"] == [HERMES]
    assert snap["busy_models"] == []
    assert snap["loading_models"] == []


@pytest.mark.asyncio
async def test_forward_edge_telemetry_with_none_loading_models_still_forwards(
    relay_integration: RemoteIntegration,
):
    """None-valued list fields must not block REL-TEL-01 forward to Master."""
    relay_integration._cached_edge_snapshot = {
        "msg_type": "telemetry.gateway.snapshot",
        "data": {
            "loaded_models": [HERMES],
            "busy_models": None,
            "loading_models": None,
        },
    }
    forward_mock = _wire_forward_mock(relay_integration)

    with patch("systems.federation.remote.api.telemetry.ingest_edge_telemetry"):
        await relay_integration._forward_edge_telemetry(
            "edge-jupiter",
            "telemetry.model.busy",
            {"model_id": HERMES},
        )

    snap = relay_integration._cached_edge_snapshot["data"]
    assert HERMES in (snap.get("busy_models") or [])
    forward_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_disconnect_clear_then_fresh_snapshot_repopulates_cache(
    relay_integration: RemoteIntegration,
):
    """After disconnect clears lists, a new GATEWAY_SNAPSHOT replaces cache state."""
    relay_integration._cached_edge_snapshot = {
        "msg_type": "telemetry.gateway.snapshot",
        "data": {
            "loaded_models": [QWEN_14B],
            "busy_models": [],
            "loading_models": [],
        },
    }

    with (
        patch(
            "systems.federation.remote.api.telemetry.handle_edge_disconnected",
            return_value=[QWEN_14B],
        ),
        patch.object(
            relay_integration,
            "_forward_edge_telemetry",
            new_callable=AsyncMock,
        ),
    ):
        await relay_integration._on_edge_disconnected()

    assert relay_integration._cached_edge_snapshot["data"]["loaded_models"] == []

    fresh = {
        "gateway_id": "gw-jupiter",
        "loaded_models": [HERMES],
        "busy_models": [],
        "loading_models": [],
        "available_vram_mb": 5000,
    }

    with patch("systems.federation.remote.api.telemetry.ingest_edge_telemetry"):
        await relay_integration._forward_edge_telemetry(
            "edge-jupiter",
            "telemetry.gateway.snapshot",
            fresh,
        )

    snap = relay_integration._cached_edge_snapshot["data"]
    assert snap["loaded_models"] == [HERMES]
    assert snap["available_vram_mb"] == 5000
