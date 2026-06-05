"""Unit tests for POST /api/v1/team/handoff.

Covers the full admission, pointer-body, and thread-creation surface.
Agent-bus is mocked at the handoff.py import site.
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from .admission import (
    FrontierEndpointError,
    resolve_handoff_contract,
    resolve_handoff_target,
    resolve_web_handoff_seat,
)
from .handoff import (
    _slug_from_subject,
    build_pointer_body,
    create_handoff_thread,
    validate_packet,
)
from .route import TeamHandoffBody, team_router

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_GOOD_PACKET = "universal-llm-gateway/tmp/reviews/smoke-packet.md"
_GOOD_SUBJECT = "test handoff"

# Fully-conformant six-block packet (incl. <mcp_capabilities> + acceptance) so a
# single on-disk file satisfies every happy-path route test (consult, implement,
# web seat, cursor seat).
_CONFORMANT_PACKET = """\
<scope>Goal: x. Selection mode: targeted.</scope>
<invariants>[scope] every changed line traces to task.</invariants>
<task_guidance>## Acceptance criteria
1. It works.</task_guidance>
<corpus>the artifact</corpus>
<mcp_capabilities>You have MCP. Cite tool calls.</mcp_capabilities>
<output_format>Reply on thread.</output_format>
"""

# 1296-style improvised packet: numbered sections, missing <corpus> + <mcp_capabilities>.
_BAD_1296_PACKET = """\
<scope>Goal: improvised.</scope>
<invariants>[scope] none.</invariants>
<task_guidance>1. do the thing
2. do the other</task_guidance>
<output_format>Reply.</output_format>
"""


def _write_packet(root: Path, rel: str, text: str) -> None:
    dest = root / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8")


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
def _handoff_app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> FastAPI:
    """Minimal FastAPI app with team_router; get_proxy mocked via sys.modules.

    The route imports get_proxy inside the handler body via a local
    ``from systems.proxy.dependencies import get_proxy``. We inject a fake
    module into sys.modules so that import succeeds without requiring the
    full Stargate runtime.

    A conformant packet is written under an injected ``PROJECT_ROOT`` so the
    admission lint (``validate_packet``) passes on every happy-path route test.
    """
    monkeypatch.setenv("PROJECT_ROOT", str(tmp_path))
    _write_packet(tmp_path, _GOOD_PACKET, _CONFORMANT_PACKET)

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
# H1 — web-consult role admitted, thread created, all four fields present
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_h1_web_consult_admitted_thread_created(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALLOW_UNSET_AGENT_BUS_TOKEN", "true")
    _patch_bus(monkeypatch, _make_bus_transport(thread_id="bus-thread-42"))

    thread_id = await create_handoff_thread(
        request_id="req-h1",
        to_agent="claude-web",
        subject=_GOOD_SUBJECT,
        pointer_body="body",
        caller_agent=None,
        tags=None,
        handoff_contract="consult",
    )
    assert thread_id == "bus-thread-42"


def test_h1a_route_web_consult_pointer_includes_consult_contract(
    monkeypatch: pytest.MonkeyPatch,
    _handoff_app: FastAPI,
) -> None:
    """role=web-consult posts pointer with Contract: consult line to agent-bus."""
    monkeypatch.setenv("ALLOW_UNSET_AGENT_BUS_TOKEN", "true")
    captured: dict[str, Any] = {}
    _patch_bus(monkeypatch, _capturing_bus_transport(captured, thread_id="bus-ptr"))

    client = TestClient(_handoff_app, raise_server_exceptions=False)
    resp = client.post(
        "/api/v1/team/handoff",
        json={
            "op": "handoff",
            "role": "web-consult",
            "packet_path": _GOOD_PACKET,
            "subject": _GOOD_SUBJECT,
        },
    )
    assert resp.status_code == 200, resp.text
    body_text = captured["payload"]["body"]
    assert "Contract: consult" in body_text
    assert _GOOD_PACKET in body_text


def test_h1a_route_web_consult_push_reminder_mentions_web_push(
    monkeypatch: pytest.MonkeyPatch,
    _handoff_app: FastAPI,
) -> None:
    """role=web-consult → claude-web; push_reminder tells operator to push bus message."""
    monkeypatch.setenv("ALLOW_UNSET_AGENT_BUS_TOKEN", "true")
    _patch_bus(monkeypatch, _make_bus_transport(thread_id="bus-thread-web"))

    client = TestClient(_handoff_app, raise_server_exceptions=False)
    resp = client.post(
        "/api/v1/team/handoff",
        json={
            "op": "handoff",
            "role": "web-consult",
            "packet_path": _GOOD_PACKET,
            "subject": _GOOD_SUBJECT,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["to_agent"] == "claude-web"
    assert body["handoff_contract"] == "consult"
    assert "push" in body["push_reminder"].lower()
    assert "web claude" in body["push_reminder"].lower()
    assert body["poll_hint"]["arguments"]["from_agent"] == "claude-web"


def test_h1_route_web_consult_returns_all_fields(
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
            "role": "web-consult",
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
            "role": "web-consult",
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
    assert data["poll_hint"]["arguments_json"] == json.dumps(
        data["poll_hint"]["arguments"],
        separators=(",", ":"),
    )
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
            "role": "web-consult",
            "packet_path": _GOOD_PACKET,
            "subject": _GOOD_SUBJECT,
        },
    )
    data = resp.json()
    assert {"thread_id", "subject", "to_agent", "push_reminder"} <= set(data)


# ---------------------------------------------------------------------------
# H1b — cursor-consult / claude-cursor seat (manual, non-dispatchable)
# ---------------------------------------------------------------------------


def test_h1b_cursor_consult_resolves_claude_cursor() -> None:
    to_agent, _family, platform = resolve_web_handoff_seat(
        "cursor-consult", request_id="req-c1"
    )
    assert to_agent == "claude-cursor"
    assert platform == "cursor"


def test_h1b_claude_cursor_seat_slug_profile_resolves() -> None:
    """Seat slug resolves at profile layer; handoff route rejects it (roster only)."""
    to_agent, _family, platform = resolve_web_handoff_seat(
        "claude-cursor", request_id="req-c2"
    )
    assert to_agent == "claude-cursor"
    assert platform == "cursor"


@pytest.mark.parametrize("role", ["lead", "cursor-lead", "implementer"])
def test_retired_handoff_roster_slug_rejected(role: str) -> None:
    with pytest.raises(FrontierEndpointError) as exc_info:
        resolve_handoff_target(role=role, request_id="req-retired")
    err = exc_info.value
    assert err.status_code == 422
    assert err.code == "handoff_role_invalid"


@pytest.mark.parametrize("role", ["claude-web", "claude-cursor", "web", "web-claude"])
def test_handoff_seat_alias_role_rejected(role: str) -> None:
    with pytest.raises(FrontierEndpointError) as exc_info:
        resolve_handoff_target(role=role, request_id="req-alias")
    err = exc_info.value
    assert err.status_code == 422
    assert err.code == "handoff_role_invalid"


def test_handoff_seat_alias_route_rejected(
    monkeypatch: pytest.MonkeyPatch,
    _handoff_app: FastAPI,
) -> None:
    monkeypatch.setenv("ALLOW_UNSET_AGENT_BUS_TOKEN", "true")
    client = TestClient(_handoff_app, raise_server_exceptions=False)
    resp = client.post(
        "/api/v1/team/handoff",
        json={
            "op": "handoff",
            "role": "claude-web",
            "packet_path": _GOOD_PACKET,
            "subject": _GOOD_SUBJECT,
        },
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "handoff_role_invalid"


def test_h1b_route_cursor_consult_push_reminder_mentions_cursor(
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
            "role": "cursor-consult",
            "packet_path": _GOOD_PACKET,
            "subject": _GOOD_SUBJECT,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["to_agent"] == "claude-cursor"
    assert "cursor" in body["push_reminder"].lower()


# ---------------------------------------------------------------------------
# H1c — cursor-implement role (claude/cursor manual seat, implement contract)
# ---------------------------------------------------------------------------


def test_h1c_cursor_implement_resolves_claude_cursor() -> None:
    """cursor-implement: admitted handoff, resolves to claude-cursor."""
    to_agent, _family, platform = resolve_web_handoff_seat(
        "cursor-implement", request_id="req-i1"
    )
    assert to_agent == "claude-cursor"
    assert platform == "cursor"


def test_h1c_route_cursor_implement_push_reminder_mentions_cursor(
    monkeypatch: pytest.MonkeyPatch,
    _handoff_app: FastAPI,
) -> None:
    monkeypatch.setenv("ALLOW_UNSET_AGENT_BUS_TOKEN", "true")
    _patch_bus(monkeypatch, _make_bus_transport(thread_id="bus-thread-impl"))

    client = TestClient(_handoff_app, raise_server_exceptions=False)
    resp = client.post(
        "/api/v1/team/handoff",
        json={
            "op": "handoff",
            "role": "cursor-implement",
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
            handoff_contract="consult",
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
        handoff_contract="consult",
    )
    assert _GOOD_PACKET in result
    assert _GOOD_SUBJECT in result
    lines = result.splitlines()
    assert len(lines) <= 25


# ---------------------------------------------------------------------------
# H6 — extra="forbid" + model admission
# ---------------------------------------------------------------------------


def test_h6_provider_model_role_on_handoff_rejected() -> None:
    """Provider model IDs are not handoff roster roles."""
    with pytest.raises(FrontierEndpointError) as exc_info:
        resolve_handoff_target(
            role="anthropic/claude-sonnet-4-6",
            request_id="req-h6",
        )
    err = exc_info.value
    assert err.status_code == 422
    assert err.code == "handoff_role_invalid"


def test_h6_extra_field_messages_rejected() -> None:
    with pytest.raises(ValidationError):
        TeamHandoffBody(
            op="handoff",
            role="web-consult",
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
            handoff_contract="consult",
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
            handoff_contract="consult",
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


# ---------------------------------------------------------------------------
# HC — role-derived contract (no request-side handoff_contract or model)
# ---------------------------------------------------------------------------


def _capturing_bus_transport(
    captured: dict[str, Any],
    thread_id: str = "thread-99",
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"thread": {"id": thread_id}, "turn": {"turn_number": 1}},
        )

    return httpx.MockTransport(handler)


def test_hc1_web_consult_no_contract_defaults_consult(
    monkeypatch: pytest.MonkeyPatch,
    _handoff_app: FastAPI,
) -> None:
    """role=web-consult → consult/role_default. Seat=claude-web."""
    monkeypatch.setenv("ALLOW_UNSET_AGENT_BUS_TOKEN", "true")
    _patch_bus(monkeypatch, _make_bus_transport(thread_id="bus-c1"))

    client = TestClient(_handoff_app, raise_server_exceptions=False)
    resp = client.post(
        "/api/v1/team/handoff",
        json={
            "op": "handoff",
            "role": "web-consult",
            "packet_path": _GOOD_PACKET,
            "subject": _GOOD_SUBJECT,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["handoff_contract"] == "consult"
    assert body["handoff_contract_source"] == "role_default"
    assert body["resolved_handoff_seat"] == "claude-web"


def test_hc1_web_consult_no_contract_tag_consult(
    monkeypatch: pytest.MonkeyPatch,
    _handoff_app: FastAPI,
) -> None:
    """Default tags include contract:consult for a role-default consult."""
    monkeypatch.setenv("ALLOW_UNSET_AGENT_BUS_TOKEN", "true")
    captured: dict[str, Any] = {}
    _patch_bus(monkeypatch, _capturing_bus_transport(captured, thread_id="bus-c1t"))

    client = TestClient(_handoff_app, raise_server_exceptions=False)
    client.post(
        "/api/v1/team/handoff",
        json={
            "op": "handoff",
            "role": "web-consult",
            "packet_path": _GOOD_PACKET,
            "subject": _GOOD_SUBJECT,
        },
    )
    assert "contract:consult" in captured["payload"]["tags"]


def test_hc2_handoff_contract_field_rejected(
    monkeypatch: pytest.MonkeyPatch,
    _handoff_app: FastAPI,
) -> None:
    """Request-side handoff_contract is forbidden (extra=forbid)."""
    monkeypatch.setenv("ALLOW_UNSET_AGENT_BUS_TOKEN", "true")
    client = TestClient(_handoff_app, raise_server_exceptions=False)
    resp = client.post(
        "/api/v1/team/handoff",
        json={
            "op": "handoff",
            "role": "web-consult",
            "packet_path": _GOOD_PACKET,
            "subject": _GOOD_SUBJECT,
            "handoff_contract": "implement",
        },
    )
    assert resp.status_code == 422


def test_hc2b_model_field_rejected(
    monkeypatch: pytest.MonkeyPatch,
    _handoff_app: FastAPI,
) -> None:
    """Request-side model is forbidden on handoff (extra=forbid)."""
    monkeypatch.setenv("ALLOW_UNSET_AGENT_BUS_TOKEN", "true")
    client = TestClient(_handoff_app, raise_server_exceptions=False)
    resp = client.post(
        "/api/v1/team/handoff",
        json={
            "op": "handoff",
            "role": "cursor-implement",
            "model": "claude-cursor",
            "packet_path": _GOOD_PACKET,
            "subject": _GOOD_SUBJECT,
        },
    )
    assert resp.status_code == 422


def test_hc3_cursor_implement_defaults_implement() -> None:
    """role=cursor-implement → implement (RoleProfile)."""
    contract, source = resolve_handoff_contract(role="cursor-implement", request_id="req-c3")
    assert contract == "implement"
    assert source == "role_default"


def test_hc4b_web_consult_consult() -> None:
    """role=web-consult → consult."""
    contract, source = resolve_handoff_contract(role="web-consult", request_id="req-c4b")
    assert contract == "consult"
    assert source == "role_default"


def test_hc5_cursor_implement_route(
    monkeypatch: pytest.MonkeyPatch,
    _handoff_app: FastAPI,
) -> None:
    """role=cursor-implement → claude-cursor + implement."""
    monkeypatch.setenv("ALLOW_UNSET_AGENT_BUS_TOKEN", "true")
    _patch_bus(monkeypatch, _make_bus_transport(thread_id="bus-c5"))

    client = TestClient(_handoff_app, raise_server_exceptions=False)
    resp = client.post(
        "/api/v1/team/handoff",
        json={
            "op": "handoff",
            "role": "cursor-implement",
            "packet_path": _GOOD_PACKET,
            "subject": _GOOD_SUBJECT,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["resolved_model"] == "claude-cursor"
    assert body["handoff_contract"] == "implement"
    assert body["handoff_contract_source"] == "role_default"


def test_hc5b_cursor_consult_consult(
    monkeypatch: pytest.MonkeyPatch,
    _handoff_app: FastAPI,
) -> None:
    """role=cursor-consult → consult."""
    monkeypatch.setenv("ALLOW_UNSET_AGENT_BUS_TOKEN", "true")
    _patch_bus(monkeypatch, _make_bus_transport(thread_id="bus-c5b"))

    client = TestClient(_handoff_app, raise_server_exceptions=False)
    resp = client.post(
        "/api/v1/team/handoff",
        json={
            "op": "handoff",
            "role": "cursor-consult",
            "packet_path": _GOOD_PACKET,
            "subject": _GOOD_SUBJECT,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["resolved_model"] == "claude-cursor"
    assert body["handoff_contract"] == "consult"
    assert body["handoff_contract_source"] == "role_default"


def test_hc5d_cursor_implement_resolves() -> None:
    to_agent, _f, platform, resolved = resolve_handoff_target(
        role="cursor-implement",
        request_id="req-agree",
    )
    assert to_agent == "claude-cursor"
    assert resolved == "claude-cursor"
    assert platform == "cursor"


def test_hc6_pointer_body_implement_contract_line() -> None:
    result = build_pointer_body(
        request_id="req-c6",
        packet_path=_GOOD_PACKET,
        subject=_GOOD_SUBJECT,
        pointer_body=None,
        handoff_contract="implement",
    )
    assert "Contract: bound implementation" in result
    assert len(result.splitlines()) <= 25


def test_hc6b_pointer_body_override_skips_contract_line() -> None:
    """Caller-supplied pointer_body is used verbatim — no contract line injected."""
    result = build_pointer_body(
        request_id="req-c6b",
        packet_path=_GOOD_PACKET,
        subject=_GOOD_SUBJECT,
        pointer_body="custom body",
        handoff_contract="implement",
    )
    assert result == "custom body"


@pytest.mark.asyncio
async def test_hc7_caller_tags_get_contract_appended(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Caller-supplied tags are preserved and contract:{value} is appended."""
    monkeypatch.setenv("ALLOW_UNSET_AGENT_BUS_TOKEN", "true")
    captured: dict[str, Any] = {}
    _patch_bus(monkeypatch, _capturing_bus_transport(captured, thread_id="bus-c7"))

    await create_handoff_thread(
        request_id="req-c7",
        to_agent="claude-web",
        subject=_GOOD_SUBJECT,
        pointer_body="body",
        caller_agent=None,
        tags=["custom:tag"],
        handoff_contract="implement",
    )
    tags = captured["payload"]["tags"]
    assert "custom:tag" in tags
    assert "contract:implement" in tags


# ---------------------------------------------------------------------------
# PV — validate_packet admission lint (Phase 4)
# ---------------------------------------------------------------------------

_PV_REL = "universal-llm-gateway/tmp/reviews/pv-packet.md"


def test_pv_missing_file_rejected(tmp_path: Path) -> None:
    with pytest.raises(FrontierEndpointError) as exc_info:
        validate_packet(
            request_id="req-pv1",
            packet_path=_PV_REL,
            to_agent="claude-web",
            handoff_contract="consult",
            workspaces_root=tmp_path,
        )
    err = exc_info.value
    assert err.status_code == 422
    assert err.code == "handoff_packet_missing"
    assert "architecture-handoff-protocol.mdc" in err.reason


def test_pv_conformant_consult_passes(tmp_path: Path) -> None:
    _write_packet(tmp_path, _PV_REL, _CONFORMANT_PACKET)
    validate_packet(
        request_id="req-pv2",
        packet_path=_PV_REL,
        to_agent="claude-web",
        handoff_contract="consult",
        workspaces_root=tmp_path,
    )


def test_pv_consult_without_acceptance_passes(tmp_path: Path) -> None:
    """Consult (role=web-consult) does not require acceptance criteria in task_guidance."""
    consult_packet = _CONFORMANT_PACKET.replace(
        "## Acceptance criteria\n1. It works.", "Review questions and risks."
    )
    _write_packet(tmp_path, _PV_REL, consult_packet)
    validate_packet(
        request_id="req-pv2a",
        packet_path=_PV_REL,
        to_agent="claude-web",
        handoff_contract="consult",
        workspaces_root=tmp_path,
    )


def test_pv_prefixed_path_when_project_root_is_repo(tmp_path: Path) -> None:
    """Stargate PROJECT_ROOT=repo; callers still pass workspaces-prefixed paths."""
    repo_root = tmp_path / "universal-llm-gateway"
    _write_packet(repo_root, "tmp/reviews/pv-packet.md", _CONFORMANT_PACKET)
    validate_packet(
        request_id="req-pv2b",
        packet_path=_PV_REL,
        to_agent="claude-web",
        handoff_contract="consult",
        workspaces_root=repo_root,
    )


def test_pv_repo_relative_path_when_project_root_is_repo(tmp_path: Path) -> None:
    repo_root = tmp_path / "universal-llm-gateway"
    rel = "tmp/reviews/pv-packet.md"
    _write_packet(repo_root, rel, _CONFORMANT_PACKET)
    validate_packet(
        request_id="req-pv2c",
        packet_path=rel,
        to_agent="claude-cursor",
        handoff_contract="implement",
        workspaces_root=repo_root,
    )


def test_pv_1296_shape_rejected_missing_tags(tmp_path: Path) -> None:
    """Missing <corpus> + <mcp_capabilities> (web seat) → 422 with cited tags."""
    _write_packet(tmp_path, _PV_REL, _BAD_1296_PACKET)
    with pytest.raises(FrontierEndpointError) as exc_info:
        validate_packet(
            request_id="req-pv3",
            packet_path=_PV_REL,
            to_agent="claude-web",
            handoff_contract="consult",
            workspaces_root=tmp_path,
        )
    err = exc_info.value
    assert err.status_code == 422
    assert err.code == "handoff_packet_invalid"
    assert "<corpus>" in err.reason
    assert "<mcp_capabilities>" in err.reason


def test_pv_mcp_capabilities_required_for_web_seat(tmp_path: Path) -> None:
    """All five base tags present, but <mcp_capabilities> absent → reject for web."""
    packet = _CONFORMANT_PACKET.replace(
        "<mcp_capabilities>You have MCP. Cite tool calls.</mcp_capabilities>\n", ""
    )
    _write_packet(tmp_path, _PV_REL, packet)
    with pytest.raises(FrontierEndpointError) as exc_info:
        validate_packet(
            request_id="req-pv4",
            packet_path=_PV_REL,
            to_agent="claude-web",
            handoff_contract="consult",
            workspaces_root=tmp_path,
        )
    assert exc_info.value.code == "handoff_packet_invalid"
    assert "<mcp_capabilities>" in exc_info.value.reason


def test_pv_implement_without_acceptance_rejected(tmp_path: Path) -> None:
    packet = _CONFORMANT_PACKET.replace(
        "## Acceptance criteria\n1. It works.", "Just do the work."
    )
    _write_packet(tmp_path, _PV_REL, packet)
    with pytest.raises(FrontierEndpointError) as exc_info:
        validate_packet(
            request_id="req-pv5",
            packet_path=_PV_REL,
            to_agent="claude-cursor",
            handoff_contract="implement",
            workspaces_root=tmp_path,
        )
    err = exc_info.value
    assert err.status_code == 422
    assert err.code == "handoff_packet_missing_acceptance"


def test_pv_implement_with_acceptance_passes(tmp_path: Path) -> None:
    _write_packet(tmp_path, _PV_REL, _CONFORMANT_PACKET)
    validate_packet(
        request_id="req-pv6",
        packet_path=_PV_REL,
        to_agent="claude-cursor",
        handoff_contract="implement",
        workspaces_root=tmp_path,
    )


def test_pv_traversal_rejected(tmp_path: Path) -> None:
    with pytest.raises(FrontierEndpointError) as exc_info:
        validate_packet(
            request_id="req-pv7",
            packet_path="../../etc/passwd",
            to_agent="claude-web",
            handoff_contract="consult",
            workspaces_root=tmp_path,
        )
    assert exc_info.value.code == "handoff_packet_invalid"


def test_pv_route_missing_packet_returns_422(
    monkeypatch: pytest.MonkeyPatch,
    _handoff_app: FastAPI,
) -> None:
    """End-to-end: handoff route returns 422 when packet_path does not exist."""
    monkeypatch.setenv("ALLOW_UNSET_AGENT_BUS_TOKEN", "true")
    _patch_bus(monkeypatch, _make_bus_transport(thread_id="bus-pv"))

    client = TestClient(_handoff_app, raise_server_exceptions=False)
    resp = client.post(
        "/api/v1/team/handoff",
        json={
            "op": "handoff",
            "role": "web-consult",
            "packet_path": "universal-llm-gateway/tmp/reviews/does-not-exist.md",
            "subject": _GOOD_SUBJECT,
        },
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "handoff_packet_missing"
