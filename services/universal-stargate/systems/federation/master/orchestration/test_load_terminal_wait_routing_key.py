"""Terminal load wait: ModelId equality and gateway_name/remote matching."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

_stargate_root = str(Path(__file__).resolve().parents[4])
if _stargate_root not in sys.path:
    sys.path.insert(0, _stargate_root)

from model_id import ModelId  # noqa: E402

from systems.federation.master.orchestration.load_terminal_wait import (  # noqa: E402
    _gateway_matches,
    _model_matches,
    wait_for_terminal_load_event,
)


@pytest.mark.parametrize(
    ("event_model_id", "expected"),
    [
        (
            "hermes-3-llama-3-1-70b-uncensored-q4-k-m-32768-hybrid",
            "hermes-3-llama-3-1-70b-uncensored-q4-k-m-32768",
        ),
        (
            "hermes-3-llama-3-1-70b-uncensored-q4-k-m-32768",
            ModelId.parse(
                "hermes-3-llama-3-1-70b-uncensored-q4-k-m-32768-hybrid"
            ),
        ),
    ],
)
def test_model_matches_hybrid_via_model_id_equality(
    event_model_id: str, expected: str | ModelId
) -> None:
    assert _model_matches({"model_id": event_model_id}, expected) is True


def test_model_matches_rejects_unrelated_model() -> None:
    assert (
        _model_matches(
            {"model_id": "qwen3-14b-q4-k-m-40960"},
            "hermes-3-llama-3-1-70b-uncensored-q4-k-m-32768",
        )
        is False
    )


def test_gateway_matches_edge_name_and_relay_remote() -> None:
    payload = {
        "gateway_name": "edge-jupiter-gateway",
        "url": "http://relay-jupiter:9998/",
    }
    assert _gateway_matches(
        payload, "edge-jupiter-gateway", "relay-jupiter"
    )


def test_gateway_matches_rejects_unrelated_gateway() -> None:
    assert not _gateway_matches(
        {"gateway_name": "edge-saturn-gateway"},
        "edge-jupiter-gateway",
        "relay-jupiter",
    )


def test_gateway_matches_url_hostname_not_substring() -> None:
    assert not _gateway_matches(
        {"url": "http://relay-jupiter-2:9998/"},
        "edge-jupiter-gateway",
        "relay-jupiter",
    )
    assert _gateway_matches(
        {"url": "http://relay-jupiter:9998/"},
        "edge-jupiter-gateway",
        "relay-jupiter",
    )


class _FakeEvent:
    def __init__(self, payload: dict) -> None:
        self.payload = payload


class _FakeSub:
    def unsubscribe(self) -> bool:
        return True


class _FakeBus:
    def __init__(self) -> None:
        self._handlers: dict[str, list] = {}

    def subscribe_async(self, signal: str, handler, **_kwargs) -> _FakeSub:
        self._handlers.setdefault(signal, []).append(handler)
        return _FakeSub()

    async def publish(self, signal: str, payload: dict) -> None:
        for handler in self._handlers.get(signal, []):
            await handler(_FakeEvent(payload))


@pytest.mark.asyncio
async def test_terminal_wait_gateway_name_and_relay_remote_pairing() -> None:
    """Pairing: edge-jupiter gateway_name + relay-jupiter remote (inferred)."""
    from src.scheduling.events.model_lifecycle import MODEL_LOAD_FAILED

    model = ModelId.parse(
        "hermes-3-llama-3-1-70b-uncensored-q4-k-m-32768-hybrid"
    )
    payload = {
        "model_id": str(model),
        "error": "RESOURCE UNAVAILABLE: VRAM: need 30184MB, have 11980MB",
        "gateway_name": "edge-jupiter-gateway",
        "url": "http://relay-jupiter:9998/",
        "gateway_state_snapshot": {
            "resources": {"available_vram_mb": 11_980},
        },
    }
    bus = _FakeBus()

    async def emit() -> None:
        await asyncio.sleep(0.02)
        await bus.publish(MODEL_LOAD_FAILED, payload)

    task = asyncio.create_task(emit())
    started = asyncio.get_running_loop().time()
    resolution = await wait_for_terminal_load_event(
        bus,
        expected_model=model,
        gateway_id="edge-jupiter-gateway",
        remote_id="relay-jupiter",
        timeout_s=5.0,
    )
    await task
    elapsed = asyncio.get_running_loop().time() - started
    assert resolution is not None
    assert resolution.outcome.value == "failed"
    assert elapsed < 1.0
