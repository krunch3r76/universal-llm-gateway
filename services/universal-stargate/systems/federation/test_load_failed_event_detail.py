"""Edge forward, remote sender, and master republish carry load-failure detail."""

from __future__ import annotations

import asyncio

from model_id import ModelId
from universal_protocol.messages.telemetry import ModelLoadFailedPayload

from systems.federation.common.protocol.message import create_model_load_failed
from systems.federation.common.types import FederatedGateway
from systems.federation.edge.telemetry import create_model_lifecycle_callbacks
from systems.federation.master.integration import MasterIntegration
from systems.federation.master.manager.federated_gateway_manager import (
    FederatedGatewayManager,
)
from systems.federation.master.manager.loading_leases import LoadingLeaseTable
from systems.federation.remote.telemetry.sender import RemoteTelemetrySender
from systems.federation.remote.telemetry.wiring import _wire_model_callbacks
from systems.proxy.core.control_plane.model_lifecycle.waiting import (
    websocket_callbacks as ws_callbacks,
)
from systems.proxy.stargate.runtime.federation_startup import (
    initialize_federation_runtime,
)

# 5be8cf77 fixture: federation key is `error`, snapshots optional, no failure_reason.
FIXTURE_5BE8CF77 = {
    "model_id": "hermes-3-llama-3-1-70b-uncensored-q4-k-m-32768-hybrid",
    "error": "VRAM: need 30184MB, have 11980MB",
    "gateway_state_snapshot": {"resources": {"available_vram_mb": 11980}},
}


def test_edge_forward_dict_carries_process_text_and_reason() -> None:
    """M2: edge-mode forward dict carries both fields and bounds process text."""

    async def _run() -> None:
        captured: list[tuple[str, dict]] = []

        async def forward(msg_type: str, payload: dict) -> None:
            captured.append((msg_type, payload))

        callbacks = create_model_lifecycle_callbacks(forward)
        await callbacks["on_model_load_failed"](
            "qwen3-14b-q4-k-m-40960",
            "y" * 600,
            None,
            None,
            "timeout",
        )
        msg_type, payload = captured[0]
        assert msg_type == "telemetry.model.loading.failed"
        assert payload["error"] == "y" * 512
        assert payload["failure_reason"] == "timeout"
        assert "worker_snapshot" not in payload

    asyncio.run(_run())


def test_sender_envelope_and_wiring_carry_failure_reason() -> None:
    """M2: remote sender twin puts both fields on the federation envelope."""

    class _Sender(RemoteTelemetrySender):
        def __init__(self) -> None:
            self.sent: list = []
            self._source = None

        async def send_message(self, msg) -> None:
            self.sent.append(msg)

    class _WireSender:
        def __init__(self) -> None:
            self.calls: list[tuple] = []

        async def on_model_loading_started(self, model_id: str) -> None:
            return None

        async def on_model_loading_progress(
            self, model_id: str, phase: str, pct: int | float
        ) -> None:
            return None

        async def on_model_loaded(self, payload: dict) -> None:
            return None

        async def on_model_load_failed(
            self,
            model_id: str,
            error: str,
            worker_snapshot: dict | None = None,
            gateway_state_snapshot: dict | None = None,
            failure_reason: str | None = None,
        ) -> None:
            self.calls.append((model_id, error, failure_reason))

        async def on_model_unloaded(self, payload: dict) -> None:
            return None

    class _WS:
        def __init__(self) -> None:
            self.failed = None

        def on_model_loading_started(self, callback) -> None:
            return None

        def on_model_loading_progress(self, callback) -> None:
            return None

        def on_model_loaded(self, callback) -> None:
            return None

        def on_model_load_failed(self, callback) -> None:
            self.failed = callback

        def on_model_unloaded(self, callback) -> None:
            return None

    async def _run() -> None:
        sender = _Sender()
        await sender.on_model_load_failed(
            "qwen3-14b-q4-k-m-40960",
            "ProcessError: llama exited 1",
            failure_reason="unknown",
        )
        data = sender.sent[0].data
        assert data["error"] == "ProcessError: llama exited 1"
        assert data["failure_reason"] == "unknown"

        wire_sender = _WireSender()
        ws = _WS()
        _wire_model_callbacks(ws, wire_sender)
        assert ws.failed is not None
        await ws.failed(
            "qwen3-14b-q4-k-m-40960",
            "ProcessError: llama exited 1",
            None,
            None,
            "unknown",
        )
        await asyncio.sleep(0)
        assert wire_sender.calls == [
            ("qwen3-14b-q4-k-m-40960", "ProcessError: llama exited 1", "unknown")
        ]

    asyncio.run(_run())


def test_waiting_callback_forwards_failure_reason() -> None:
    """Edge waiting wrapper keeps failure_reason on the downstream callback."""

    async def _run() -> None:
        seen: list[str | None] = []

        async def original(
            model_id: str,
            error_message: str,
            worker_snapshot: dict | None = None,
            gateway_state_snapshot: dict | None = None,
            failure_reason: str | None = None,
        ) -> None:
            seen.append(failure_reason)

        factory = ws_callbacks.WebSocketCallbackFactory.__new__(
            ws_callbacks.WebSocketCallbackFactory
        )
        factory.gateway_name = "edge-jupiter-gateway"
        factory.pending_loads = {}
        factory.global_load_coordinator = None
        factory.to_event_key_func = lambda _model_id: "qwen3-14b"
        callback = factory.create_on_load_failed(original)
        await callback(
            "qwen3-14b-q4-k-m-40960",
            "ProcessError: llama exited 1",
            None,
            None,
            "oom",
        )
        assert seen == ["oom"]

    asyncio.run(_run())


def _manager() -> FederatedGatewayManager:
    mgr = FederatedGatewayManager.__new__(FederatedGatewayManager)
    mgr._event_bus = None
    mgr._load_resource_baseline = {}
    mgr._loading_leases = LoadingLeaseTable()
    mgr._gateways = {
        "edge-jupiter-gateway": FederatedGateway(
            gateway_id="edge-jupiter-gateway",
            remote_stargate_id="relay-jupiter",
            remote_stargate_url="http://relay-jupiter:9998",
            node_id="jupiter",
            vram_free_mb=30_000,
            vram_total_mb=32_607,
            ram_free_mb=32_000,
            ram_total_mb=64_000,
            loaded_models=frozenset({ModelId.parse("qwen3-14b-q4-k-m-40960")}),
        )
    }
    return mgr


class _Bus:
    def __init__(self) -> None:
        self.events: list = []

    async def publish_nowait(self, event) -> None:
        self.events.append(event)


def test_master_republish_carries_both_fields() -> None:
    """M3: master ModelLoadingFailed republish carries process text and reason."""

    async def _run() -> None:
        mgr = _manager()
        bus = _Bus()
        mgr._event_bus = bus
        gw = mgr._gateways["edge-jupiter-gateway"]
        mgr._apply_model_load_failed(
            gw,
            {
                "model_id": "qwen3-14b-q4-k-m-40960",
                "error": "z" * 600,
                "failure_reason": "oom",
            },
        )
        await asyncio.sleep(0)
        payload = bus.events[0].payload
        assert payload["error"] == "z" * 512
        assert payload["failure_reason"] == "oom"

    asyncio.run(_run())


def test_old_payload_without_failure_reason_is_accepted() -> None:
    """M4: 5be8cf77 fixture (`error` only, optional snapshot) does not raise."""
    parsed = ModelLoadFailedPayload.from_dict(FIXTURE_5BE8CF77)
    assert parsed.error == FIXTURE_5BE8CF77["error"]
    assert parsed.failure_reason is None
    assert parsed.gateway_state_snapshot == FIXTURE_5BE8CF77["gateway_state_snapshot"]

    async def _run() -> None:
        mgr = _manager()
        bus = _Bus()
        mgr._event_bus = bus
        gw = mgr._gateways["edge-jupiter-gateway"]
        mgr._apply_model_load_failed(gw, dict(FIXTURE_5BE8CF77))
        await asyncio.sleep(0)
        payload = bus.events[0].payload
        assert payload["error"] == FIXTURE_5BE8CF77["error"]
        assert "failure_reason" not in payload

    asyncio.run(_run())


def test_extra_keys_and_failure_reason_do_not_raise() -> None:
    """M5: new failure_reason is accepted; unknown keys are ignored."""
    data = {
        **FIXTURE_5BE8CF77,
        "failure_reason": "timeout",
        "context": {"token": "SECRETTOKEN"},
        "not_a_field": 1,
    }
    parsed = ModelLoadFailedPayload.from_dict(data)
    assert parsed.failure_reason == "timeout"
    assert parsed.error == FIXTURE_5BE8CF77["error"]
    assert not hasattr(parsed, "context")
    envelope = create_model_load_failed(
        model_id=str(parsed.model_id),
        error=parsed.error,
        failure_reason=parsed.failure_reason,
        gateway_state_snapshot=parsed.gateway_state_snapshot,
    )
    assert envelope.data["failure_reason"] == "timeout"
    assert "context" not in envelope.data
    assert "SECRETTOKEN" not in str(envelope.data["error"])


def test_protocol_bounds_oversize_process_text() -> None:
    """M6: federation payload bounds process text at 512 characters."""
    payload = ModelLoadFailedPayload.from_dict(
        {"model_id": "qwen3-14b-q4-k-m-40960", "error": "é" * 600}
    )
    assert len(payload.error or "") == 512
    assert payload.to_dict()["error"] == "é" * 512


def test_boot_smoke_master_local_edge_register() -> None:
    """M7: master startup import includes the local-edge register path."""
    assert callable(initialize_federation_runtime)
    assert callable(MasterIntegration._register_local_edge_remote)
    assert callable(MasterIntegration._start_local_edge_client)
