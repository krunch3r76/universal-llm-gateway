"""Tests for standing-thread liveness alert delivery."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

_stargate_root = str(Path(__file__).resolve().parents[4])
if _stargate_root not in sys.path:
    sys.path.insert(0, _stargate_root)

from src.scheduling.events.consumers.liveness_alert_bridge import (  # noqa: E402
    LivenessAlertBridge,
    last_gateway_liveness_state_from_turns,
    recovered_subject,
    stale_subject,
)
from src.scheduling.events.federation_signaling import (  # noqa: E402
    FederationGatewayLivenessStale,
    FederationGatewayRecovered,
)

_GATEWAY = "edge-jupiter-gateway"
_STANDING_THREAD = "8842"
_SLUG = "federation-liveness-alerts"


class _FakeBus:
    def subscribe_async(self, *_args: Any, **_kwargs: Any) -> None:
        pass


def _stale_event(gateway_id: str = _GATEWAY):
    return FederationGatewayLivenessStale(
        gateway_id=gateway_id,
        heartbeat_age_ms=120_000,
        threshold_ms=60_000,
        last_heartbeat_iso="2026-09-28T00:00:00Z",
        backend_type="federated",
    )


def _recovered_event(gateway_id: str = _GATEWAY):
    return FederationGatewayRecovered(
        gateway_id=gateway_id,
        kind="liveness",
        reason="heartbeat resumed",
        downtime_ms=45_000,
    )


def _bridge_with_transport(
    handler: httpx.MockTransport,
    monkeypatch: pytest.MonkeyPatch,
) -> LivenessAlertBridge:
    monkeypatch.setenv("AGENT_BUS_TOKEN", "test-token")
    bridge = LivenessAlertBridge(_FakeBus())
    monkeypatch.setattr(
        "src.scheduling.events.consumers.liveness_alert_bridge.make_async_client",
        lambda *_args, **_kwargs: httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="http://localhost",
        ),
    )
    return bridge


def test_last_gateway_liveness_state_from_turns_picks_latest() -> None:
    turns = [
        {
            "turn_number": 1,
            "from": "stargate-liveness-watchdog",
            "subject": stale_subject(_GATEWAY),
        },
        {
            "turn_number": 2,
            "from": "stargate-liveness-watchdog",
            "subject": recovered_subject(_GATEWAY),
        },
        {
            "turn_number": 3,
            "from": "stargate-liveness-watchdog",
            "subject": stale_subject(_GATEWAY),
        },
    ]
    assert (
        last_gateway_liveness_state_from_turns(turns, gateway_id=_GATEWAY) == "stale"
    )


@pytest.mark.asyncio
async def test_ac1_stale_and_recovery_same_thread_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two successful emits for one gateway share one standing thread id."""
    posted_threads: list[str] = []
    turn_history: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/threads":
            if turn_history:
                return httpx.Response(
                    200,
                    json={"threads": [{"id": _STANDING_THREAD, "slug": _SLUG}]},
                )
            return httpx.Response(200, json={"threads": []})
        if request.method == "GET" and request.url.path == "/turns":
            return httpx.Response(200, json={"turns": list(turn_history)})
        if request.method == "POST" and request.url.path == "/threads/send":
            body = json.loads(request.content)
            if body.get("new_slug") == _SLUG:
                posted_threads.append(_STANDING_THREAD)
                turn_history.append(
                    {
                        "turn_number": 1,
                        "from": body["from"],
                        "subject": body["subject"],
                    }
                )
                return httpx.Response(
                    201,
                    json={
                        "send_path": "new_thread",
                        "thread": {"id": _STANDING_THREAD, "slug": _SLUG},
                        "turn": {"turn_number": 1},
                    },
                )
            thread = body["thread"]
            posted_threads.append(thread)
            turn_history.append(
                {
                    "turn_number": len(turn_history) + 1,
                    "from": body["from"],
                    "subject": body["subject"],
                }
            )
            return httpx.Response(
                201,
                json={
                    "send_path": "continue",
                    "thread": {"id": thread},
                    "turn": {"turn_number": len(turn_history)},
                },
            )
        return httpx.Response(404)

    bridge = _bridge_with_transport(handler, monkeypatch)
    await bridge._on_liveness_stale(_stale_event())
    await bridge._on_gateway_recovered(_recovered_event())

    assert posted_threads == [_STANDING_THREAD, _STANDING_THREAD]
    assert len(turn_history) == 2


@pytest.mark.asyncio
async def test_ac2_stale_then_stale_skips_second(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    turn_history: list[dict[str, Any]] = [
        {
            "turn_number": 1,
            "from": "stargate-liveness-watchdog",
            "subject": stale_subject(_GATEWAY),
        }
    ]
    post_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal post_count
        if request.method == "GET" and request.url.path == "/threads":
            return httpx.Response(
                200,
                json={"threads": [{"id": _STANDING_THREAD, "slug": _SLUG}]},
            )
        if request.method == "GET" and request.url.path == "/turns":
            return httpx.Response(200, json={"turns": turn_history})
        if request.method == "POST" and request.url.path == "/threads/send":
            post_count += 1
            body = json.loads(request.content)
            turn_history.append(
                {
                    "turn_number": len(turn_history) + 1,
                    "from": body["from"],
                    "subject": body["subject"],
                }
            )
            return httpx.Response(201, json={"thread": {"id": _STANDING_THREAD}})
        return httpx.Response(404)

    bridge = _bridge_with_transport(handler, monkeypatch)
    bridge._standing_thread_id = _STANDING_THREAD
    await bridge._on_liveness_stale(_stale_event())
    assert post_count == 0


@pytest.mark.asyncio
async def test_ac2_fresh_after_stale_posts_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    turn_history: list[dict[str, Any]] = [
        {
            "turn_number": 1,
            "from": "stargate-liveness-watchdog",
            "subject": stale_subject(_GATEWAY),
        }
    ]
    subjects: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/threads":
            return httpx.Response(
                200,
                json={"threads": [{"id": _STANDING_THREAD, "slug": _SLUG}]},
            )
        if request.method == "GET" and request.url.path == "/turns":
            return httpx.Response(200, json={"turns": turn_history})
        if request.method == "POST" and request.url.path == "/threads/send":
            body = json.loads(request.content)
            subjects.append(body["subject"])
            turn_history.append(
                {
                    "turn_number": len(turn_history) + 1,
                    "from": body["from"],
                    "subject": body["subject"],
                }
            )
            return httpx.Response(201, json={"thread": {"id": _STANDING_THREAD}})
        return httpx.Response(404)

    bridge = _bridge_with_transport(handler, monkeypatch)
    bridge._standing_thread_id = _STANDING_THREAD
    await bridge._on_gateway_recovered(_recovered_event())
    assert subjects == [recovered_subject(_GATEWAY)]


@pytest.mark.asyncio
async def test_ac3_restart_rereads_thread_for_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """New bridge instance (no cache) still posts recovery from thread history."""
    turn_history: list[dict[str, Any]] = [
        {
            "turn_number": 1,
            "from": "stargate-liveness-watchdog",
            "subject": stale_subject(_GATEWAY),
        }
    ]
    subjects: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/threads":
            return httpx.Response(
                200,
                json={"threads": [{"id": _STANDING_THREAD, "slug": _SLUG}]},
            )
        if request.method == "GET" and request.url.path == "/turns":
            return httpx.Response(200, json={"turns": turn_history})
        if request.method == "POST" and request.url.path == "/threads/send":
            body = json.loads(request.content)
            subjects.append(body["subject"])
            return httpx.Response(201, json={"thread": {"id": body["thread"]}})
        return httpx.Response(404)

    restarted = _bridge_with_transport(handler, monkeypatch)
    assert restarted._standing_thread_id is None
    await restarted._on_gateway_recovered(_recovered_event())
    assert subjects == [recovered_subject(_GATEWAY)]
