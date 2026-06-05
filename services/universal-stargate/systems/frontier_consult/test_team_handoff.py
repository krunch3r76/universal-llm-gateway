"""Unit tests for POST /api/v1/team/handoff.

Covers the full admission, pointer-body, and thread-creation surface.
Agent-bus is mocked at the handoff.py import site.
"""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from .admission import FrontierEndpointError, resolve_web_handoff_seat
from .handoff import _slug_from_subject, build_pointer_body, create_handoff_thread
from .route import TeamHandoffBody, team_router

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_GOOD_PACKET = "universal-llm-gateway/tmp/reviews/smoke-packet.md"
_GOOD_SUBJECT = "test handoff"


def _make_bus_transport(
    thread_id: str = "thread-99",
    status_code: int = 200,
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if status_code >= 400:
            return httpx.Response(status_code, json={"detail": "error"})
        return httpx.Response(
            200,
            json={
                "thread": {"id": thread_id},
                "turn": {"turn_number": 1},
            },
        )

    return httpx.MockTransport(handler)


def _patch_bus(
    monkeypatch: pytest.MonkeyPatch,
    transport: httpx.MockTransport,
) -> None:
    """Patch make_async_client at the handoff.py import site."""
    monkeypatch.setattr(
        "systems.frontier_consult.handoff.make_async_client",
        lambda *a, **k: httpx.AsyncClient(
            transport=transport, base_url="http://localhost"
        ),
    )


@pytest.fixture()
def _handoff_app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    """Minimal FastAPI app with team_router; get_proxy mocked via sys.modules.

    The route imports get_proxy inside the handler body via a local
    ``from systems.proxy.dependencies import get_proxy``. We inject a fake
    module into sys.modules so that import succeeds without requiring the
    full Stargate runtime.
    """
    mock_proxy = MagicMock()
    mock_proxy.event_bus = None

    fake_deps = types.ModuleType("systems.proxy.dependencies")
    fake_deps.get_proxy = lambda: mock_proxy  # type: ignore[attr-defined]

    # Ensure intermediate package exists in sys.modules
    if "systems.proxy" not in sys.modules:
        fake_proxy_pkg = types.ModuleType("systems.proxy")
        monkeypatch.setitem(sys.modules, "systems.proxy", fake_proxy_pkg)

    monkeypatch.setitem(sys.modules, "systems.proxy.dependencies", fake_deps)

    app = FastAPI()
    app.include_router(team_router)
    return app


# ---------------------------------------------------------------------------
# H1 — lead role admitted, thread created, all four fields present
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_h1_lead_admitted_thread_created(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALLOW_UNSET_AGENT_BUS_TOKEN", "true")
    _patch_bus(monkeypatch, _make_bus_transport(thread_id="bus-thread-42"))

    thread_id = await create_handoff_thread(
        request_id="req-h1",
        to_agent="claude-web",
        subject=_GOOD_SUBJECT,
        pointer_body="body",
        caller_agent=None,
        tags=None,
    )
    assert thread_id == "bus-thread-42"


def test_h1_route_lead_returns_all_fields(
    monkeypatch: pytest.MonkeyPatch,
    _handoff_app: FastAPI,
) -> None:
    monkeypatch.setenv("ALLOW_UNSET_AGENT_BUS_TOKEN", "true")
    _patch_bus(monkeypatch, _make_bus_transport(thread_id="bus-thread-99"))

    client = TestClient(_handoff_app, raise_server_exceptions=False)
    resp = client.post(
        "/api/v1/team/handoff",
        json={
            "op": "handoff",
            "role": "lead",
            "packet_path": _GOOD_PACKET,
            "subject": _GOOD_SUBJECT,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["thread_id"] == "bus-thread-99"
    assert body["to_agent"] == "claude-web"
    assert body["subject"] == _GOOD_SUBJECT
    assert "bus-thread-99" in body["push_reminder"]
    assert "push" in body["push_reminder"].lower()


def test_handoff_response_includes_result_handle(
    monkeypatch: pytest.MonkeyPatch,
    _handoff_app: FastAPI,
) -> None:
    """Phase 1: handoff 200 carries result_handle / handoff_status / poll_hint."""
    monkeypatch.setenv("ALLOW_UNSET_AGENT_BUS_TOKEN", "true")
    _patch_bus(monkeypatch, _make_bus_transport(thread_id="bus-thread-99"))

    client = TestClient(_handoff_app, raise_server_exceptions=False)
    resp = client.post(
        "/api/v1/team/handoff",
        json={
            "op": "handoff",
            "role": "lead",
            "packet_path": _GOOD_PACKET,
            "subject": _GOOD_SUBJECT,
        },
    )
    assert resp.status_code == 200
    data = resp.json()

    assert data["result_handle"] == {
        "kind": "agent_bus_thread",
        "thread_id": data["thread_id"],
        "after_turn": 1,
    }
    assert data["handoff_status"] == "awaiting_first_reply"
    assert data["poll_hint"]["tool"] == "wait"
    assert data["poll_hint"]["arguments"]["thread"] == data["thread_id"]
    assert data["poll_hint"]["arguments"]["from_agent"] == data["to_agent"]
    assert data["poll_hint"]["arguments"]["completion"] == "first_reply_from"
    assert "execution_id" not in data


def test_handoff_response_backward_compatible_keys(
    monkeypatch: pytest.MonkeyPatch,
    _handoff_app: FastAPI,
) -> None:
    """Existing keys remain present and unchanged (additive contract)."""
    monkeypatch.setenv("ALLOW_UNSET_AGENT_BUS_TOKEN", "true")
    _patch_bus(monkeypatch, _make_bus_transport(thread_id="bus-thread-99"))

    client = TestClient(_handoff_app, raise_server_exceptions=False)
    resp = client.post(
        "/api/v1/team/handoff",
        json={
            "op": "handoff",
            "role": "lead",
            "packet_path": _GOOD_PACKET,
            "subject": _GOOD_SUBJECT,
        },
    )
    data = resp.json()
    assert {"thread_id", "subject", "to_agent", "push_reminder"} <= set(data)


# ---------------------------------------------------------------------------
# H1b — cursor-lead / claude-cursor seat admitted (manual, non-dispatchable)
# ---------------------------------------------------------------------------


def test_h1b_cursor_lead_resolves_claude_cursor() -> None:
    to_agent, _family, platform = resolve_web_handoff_seat(
        "cursor-lead", request_id="req-c1"
    )
    assert to_agent == "claude-cursor"
    assert platform == "cursor"


def test_h1b_claude_cursor_seat_slug_admitted() -> None:
    to_agent, _family, platform = resolve_web_handoff_seat(
        "claude-cursor", request_id="req-c2"
    )
    assert to_agent == "claude-cursor"
    assert platform == "cursor"


def test_h1b_route_cursor_lead_push_reminder_mentions_cursor(
    monkeypatch: pytest.MonkeyPatch,
    _handoff_app: FastAPI,
) -> None:
    monkeypatch.setenv("ALLOW_UNSET_AGENT_BUS_TOKEN", "true")
    _patch_bus(monkeypatch, _make_bus_transport(thread_id="bus-thread-cursor"))

    client = TestClient(_handoff_app, raise_server_exceptions=False)
    resp = client.post(
        "/api/v1/team/handoff",
        json={
            "op": "handoff",
            "role": "cursor-lead",
            "packet_path": _GOOD_PACKET,
            "subject": _GOOD_SUBJECT,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["to_agent"] == "claude-cursor"
    assert "cursor" in body["push_reminder"].lower()


# ---------------------------------------------------------------------------
# H2 — dispatchable role → 422 handoff_requires_web_seat
# ---------------------------------------------------------------------------


def test_h2_dispatchable_role_rejected() -> None:
    """reviewer → gpt/api is dispatchable → admission fails."""
    with pytest.raises(FrontierEndpointError) as exc_info:
        resolve_web_handoff_seat("reviewer", request_id="req-h2")
    err = exc_info.value
    assert err.status_code == 422
    assert err.field == "role"
    assert "handoff_requires_web_seat" in err.reason or "dispatchable" in err.reason


def test_h2_route_dispatchable_role_returns_422(
    monkeypatch: pytest.MonkeyPatch,
    _handoff_app: FastAPI,
) -> None:
    monkeypatch.setenv("ALLOW_UNSET_AGENT_BUS_TOKEN", "true")
    client = TestClient(_handoff_app, raise_server_exceptions=False)
    resp = client.post(
        "/api/v1/team/handoff",
        json={
            "op": "handoff",
            "role": "reviewer",
            "packet_path": _GOOD_PACKET,
            "subject": _GOOD_SUBJECT,
        },
    )
    assert resp.status_code == 422
    body = resp.json()
    assert "error" in body


# ---------------------------------------------------------------------------
# H3 — unknown role → 422
# ---------------------------------------------------------------------------


def test_h3_unknown_role_rejected() -> None:
    with pytest.raises(FrontierEndpointError) as exc_info:
        resolve_web_handoff_seat("no-such-role-xyz", request_id="req-h3")
    err = exc_info.value
    assert err.status_code == 422
    assert err.field == "role"


# ---------------------------------------------------------------------------
# H4 — pointer_body override > 25 lines → 422
# ---------------------------------------------------------------------------


def test_h4_pointer_body_too_long_raises() -> None:
    long_body = "\n".join([f"line {i}" for i in range(26)])
    with pytest.raises(FrontierEndpointError) as exc_info:
        build_pointer_body(
            request_id="req-h4",
            packet_path=_GOOD_PACKET,
            subject=_GOOD_SUBJECT,
            pointer_body=long_body,
        )
    err = exc_info.value
    assert err.status_code == 422
    assert err.field == "pointer_body"
    assert "25" in err.reason


# ---------------------------------------------------------------------------
# H5 — default template used when pointer_body omitted; cites packet_path
# ---------------------------------------------------------------------------


def test_h5_default_template_cites_packet_path() -> None:
    result = build_pointer_body(
        request_id="req-h5",
        packet_path=_GOOD_PACKET,
        subject=_GOOD_SUBJECT,
        pointer_body=None,
    )
    assert _GOOD_PACKET in result
    assert _GOOD_SUBJECT in result
    lines = result.splitlines()
    assert len(lines) <= 25


# ---------------------------------------------------------------------------
# H6 — extra="forbid": supplying model or messages → Pydantic 422
# ---------------------------------------------------------------------------


def test_h6_extra_field_model_rejected() -> None:
    with pytest.raises(ValidationError):
        TeamHandoffBody(
            op="handoff",
            role="lead",
            packet_path=_GOOD_PACKET,
            subject=_GOOD_SUBJECT,
            model="anthropic/claude-sonnet-4-6",  # type: ignore[call-arg]
        )


def test_h6_extra_field_messages_rejected() -> None:
    with pytest.raises(ValidationError):
        TeamHandoffBody(
            op="handoff",
            role="lead",
            packet_path=_GOOD_PACKET,
            subject=_GOOD_SUBJECT,
            messages=[{"role": "user", "content": "x"}],  # type: ignore[call-arg]
        )


# ---------------------------------------------------------------------------
# H7 — agent-bus token unset + no bypass → 503
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_h7_missing_token_raises_503(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENT_BUS_TOKEN", raising=False)
    monkeypatch.delenv("ALLOW_UNSET_AGENT_BUS_TOKEN", raising=False)

    with pytest.raises(FrontierEndpointError) as exc_info:
        await create_handoff_thread(
            request_id="req-h7",
            to_agent="claude-web",
            subject=_GOOD_SUBJECT,
            pointer_body="body",
            caller_agent=None,
            tags=None,
        )
    err = exc_info.value
    assert err.status_code == 503
    assert err.field == "thread"


# ---------------------------------------------------------------------------
# H8 — agent-bus transport error → structured 5xx, not unhandled raise
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_h8_transport_error_returns_structured_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALLOW_UNSET_AGENT_BUS_TOKEN", "true")

    def _error_client(*a: object, **k: object) -> httpx.AsyncClient:
        transport = httpx.MockTransport(
            lambda r: (_ for _ in ()).throw(httpx.ConnectError("connection refused"))
        )
        return httpx.AsyncClient(transport=transport, base_url="http://localhost")

    monkeypatch.setattr(
        "systems.frontier_consult.handoff.make_async_client",
        _error_client,
    )

    with pytest.raises(FrontierEndpointError) as exc_info:
        await create_handoff_thread(
            request_id="req-h8",
            to_agent="claude-web",
            subject=_GOOD_SUBJECT,
            pointer_body="body",
            caller_agent=None,
            tags=None,
        )
    err = exc_info.value
    assert err.status_code >= 500
    assert err.field == "thread"


# ---------------------------------------------------------------------------
# Slug helper
# ---------------------------------------------------------------------------


def test_slug_from_subject_basic() -> None:
    assert _slug_from_subject("My Test Handoff!") == "my-test-handoff"


def test_slug_from_subject_long_truncated() -> None:
    long = "a" * 100
    assert len(_slug_from_subject(long)) <= 50
