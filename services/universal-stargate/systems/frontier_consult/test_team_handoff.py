"""Unit tests for POST /api/v1/team/handoff.

Covers the full admission, pointer-body, and thread-creation surface.
Agent-bus is mocked at the handoff.py import site.
"""

from __future__ import annotations

import json
import os
import sys
import types
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from implement_admission.drift_gates import DriftGateState

from .admission import (
    FrontierEndpointError,
    resolve_handoff_contract,
    resolve_handoff_seat,
    resolve_handoff_target,
    resolve_web_handoff_seat,
)
from .contract_derivation import derive_contract
from .handoff import (
    _slug_from_subject,
    build_pointer_body,
    create_handoff_thread,
    validate_packet,
)
from .implement_admission_bridge import BridgeResult
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
async def test_h1_web_consult_admitted_thread_created(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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


def test_handoff_enforcement_roster_advertises_derived_seat_map() -> None:
    """Invalid handoff role rejection carries the catalog-derived seat-map clause.

    Falsifier for roster-advertisement drift (friction 13744): the 422 reason
    must contain the live ``handoff_seat_map_clause()`` output — not a tautological
    derived==derived comparison.
    """
    from agent_seat.dispatch_role_catalog import handoff_seat_map_clause

    with pytest.raises(FrontierEndpointError) as exc_info:
        resolve_handoff_target(role="not-a-role", request_id="req-roster-map")
    err = exc_info.value
    assert err.status_code == 422
    assert err.code == "handoff_role_invalid"
    assert handoff_seat_map_clause() in err.reason


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
# H1d — web-implement role (claude/web manual seat, implement contract)
#
# Symmetric completion of the {platform}-{contract} roster (friction 13571).
# Web bound-implement previously had to route through web-consult, which derives
# the *consult* contract — so the implement guardrails (acceptance-criteria
# packet lint, implement pointer line, contract:implement tag) never fired for
# web. web-implement gives web a role-derived path to the implement contract.
# ---------------------------------------------------------------------------


def test_h1d_web_implement_resolves_claude_web() -> None:
    """web-implement: admitted handoff, resolves to claude-web."""
    to_agent, _family, platform = resolve_web_handoff_seat(
        "web-implement", request_id="req-wi1"
    )
    assert to_agent == "claude-web"
    assert platform == "web"


def test_h1d_web_implement_target_resolves() -> None:
    to_agent, _f, platform, resolved = resolve_handoff_target(
        role="web-implement",
        request_id="req-wi-target",
    )
    assert to_agent == "claude-web"
    assert resolved == "claude-web"
    assert platform == "web"


def test_h1d_web_implement_defaults_implement() -> None:
    """role=web-implement → implement (RoleProfile.default_contract)."""
    contract, source = resolve_handoff_contract(
        role="web-implement", request_id="req-wi-c"
    )
    assert contract == "implement"
    assert source == "role_default"


def test_h1d_route_web_implement_seat_and_contract(
    monkeypatch: pytest.MonkeyPatch,
    _handoff_app: FastAPI,
) -> None:
    """role=web-implement → claude-web + implement contract (role_default)."""
    monkeypatch.setenv("ALLOW_UNSET_AGENT_BUS_TOKEN", "true")
    _patch_bus(monkeypatch, _make_bus_transport(thread_id="bus-wi"))

    client = TestClient(_handoff_app, raise_server_exceptions=False)
    resp = client.post(
        "/api/v1/team/handoff",
        json={
            "op": "handoff",
            "role": "web-implement",
            "packet_path": _GOOD_PACKET,
            "subject": _GOOD_SUBJECT,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["to_agent"] == "claude-web"
    assert body["resolved_handoff_seat"] == "claude-web"
    assert body["handoff_contract"] == "implement"
    assert body["handoff_contract_source"] == "role_default"
    assert "push" in body["push_reminder"].lower()
    assert body["poll_hint"]["arguments"]["from_agent"] == "claude-web"


def test_h1d_route_web_implement_pointer_and_tag(
    monkeypatch: pytest.MonkeyPatch,
    _handoff_app: FastAPI,
) -> None:
    """web-implement posts the implement pointer line + contract:implement tag."""
    monkeypatch.setenv("ALLOW_UNSET_AGENT_BUS_TOKEN", "true")
    captured: dict[str, Any] = {}
    _patch_bus(monkeypatch, _capturing_bus_transport(captured, thread_id="bus-wi-t"))

    client = TestClient(_handoff_app, raise_server_exceptions=False)
    resp = client.post(
        "/api/v1/team/handoff",
        json={
            "op": "handoff",
            "role": "web-implement",
            "packet_path": _GOOD_PACKET,
            "subject": _GOOD_SUBJECT,
        },
    )
    assert resp.status_code == 200, resp.text
    assert "Contract: bound implementation" in captured["payload"]["body"]
    assert "contract:implement" in captured["payload"]["tags"]


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
    contract, source = resolve_handoff_contract(
        role="cursor-implement", request_id="req-c3"
    )
    assert contract == "implement"
    assert source == "role_default"


def test_hc4b_web_consult_consult() -> None:
    """role=web-consult → consult."""
    contract, source = resolve_handoff_contract(
        role="web-consult", request_id="req-c4b"
    )
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


def test_pv_mcp_capabilities_required_for_grok_web_seat(tmp_path: Path) -> None:
    """Predicate-derived MCP seats include grok-web — packet lint applies."""
    packet = _CONFORMANT_PACKET.replace(
        "<mcp_capabilities>You have MCP. Cite tool calls.</mcp_capabilities>\n", ""
    )
    _write_packet(tmp_path, _PV_REL, packet)
    with pytest.raises(FrontierEndpointError) as exc_info:
        validate_packet(
            request_id="req-pv-grok",
            packet_path=_PV_REL,
            to_agent="grok-web",
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


def test_pv_web_implement_without_acceptance_rejected(tmp_path: Path) -> None:
    """web-implement (→ claude-web, contract=implement) enforces acceptance criteria.

    Locks in that the implement packet lint keys on the contract value + seat,
    not on a cursor-only role name — so web-implement gets the same guardrail.
    """
    packet = _CONFORMANT_PACKET.replace(
        "## Acceptance criteria\n1. It works.", "Just do the work."
    )
    _write_packet(tmp_path, _PV_REL, packet)
    with pytest.raises(FrontierEndpointError) as exc_info:
        validate_packet(
            request_id="req-pv-wi",
            packet_path=_PV_REL,
            to_agent="claude-web",
            handoff_contract="implement",
            workspaces_root=tmp_path,
        )
    err = exc_info.value
    assert err.status_code == 422
    assert err.code == "handoff_packet_missing_acceptance"


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


def test_drift_gate_a_consult_exempt(tmp_path: Path) -> None:
    _write_packet(tmp_path, _PV_REL, _CONFORMANT_PACKET)
    validate_packet(
        request_id="req-dga-consult",
        packet_path=_PV_REL,
        to_agent="claude-web",
        handoff_contract="consult",
        workspaces_root=tmp_path,
        source_ref=None,
    )


def test_drift_gate_a_present_admits(tmp_path: Path) -> None:
    _write_packet(tmp_path, _PV_REL, _CONFORMANT_PACKET)
    validate_packet(
        request_id="req-dga-present",
        packet_path=_PV_REL,
        to_agent="claude-cursor",
        handoff_contract="implement",
        workspaces_root=tmp_path,
        source_ref="todo:foo",
    )


def test_drift_gate_a_enforce_missing_source_ref(tmp_path: Path) -> None:
    _write_packet(tmp_path, _PV_REL, _CONFORMANT_PACKET)
    with patch(
        "implement_admission.drift_gates.gate_state",
        side_effect=lambda gate_id: {
            "a": DriftGateState.ENFORCE,
            "a2": DriftGateState.OFF,
        }.get(gate_id, DriftGateState.WARN),
    ):
        with pytest.raises(FrontierEndpointError) as exc_info:
            validate_packet(
                request_id="req-dga-enforce",
                packet_path=_PV_REL,
                to_agent="claude-cursor",
                handoff_contract="implement",
                workspaces_root=tmp_path,
                source_ref=None,
            )
    assert exc_info.value.code == "handoff_missing_source_ref"


def test_drift_gate_a_warn_missing_source_ref(tmp_path: Path) -> None:
    _write_packet(tmp_path, _PV_REL, _CONFORMANT_PACKET)
    with patch.dict(os.environ, {"UA_DRIFT_GATE_A": "warn"}, clear=False):
        from implement_admission.drift_gates import clear_gate_state_cache

        clear_gate_state_cache()
        validate_packet(
            request_id="req-dga-warn",
            packet_path=_PV_REL,
            to_agent="claude-cursor",
            handoff_contract="implement",
            workspaces_root=tmp_path,
            source_ref=None,
        )


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


# ---------------------------------------------------------------------------
# P2 — Phase 2 unified implement admission (source_ref wire)
# ---------------------------------------------------------------------------


class _Phase2StubCortex:
    """Stub cortex reader for source_ref handoff tests."""

    def entity_get(self, entity_id: str, **kwargs: Any) -> dict[str, Any]:  # noqa: ANN003, ARG002
        if entity_id == "decision:unified-implement-admission":
            return {
                "id": entity_id,
                "assertions": [
                    {
                        "confidence": "confirmed",
                        "superseded": False,
                        "superseded_by": None,
                    },
                ],
            }
        attrs: dict[str, Any] = {
            "content_hash": "sha256:fixture",
            "acceptance_criteria": ["AC1", "AC2"],
            "files_expected": ["a.py", "b.py"],
        }
        return {"id": entity_id, "name": entity_id, "attributes": attrs}


class _Phase2BelievedOnlyCortex(_Phase2StubCortex):
    def entity_get(self, entity_id: str, **kwargs: Any) -> dict[str, Any]:  # noqa: ANN003, ARG002
        if entity_id == "decision:unified-implement-admission":
            return {
                "id": entity_id,
                "assertions": [
                    {
                        "confidence": "believed",
                        "superseded": False,
                        "superseded_by": None,
                    },
                ],
            }
        return super().entity_get(entity_id, **kwargs)


def _patch_phase2_reader(
    monkeypatch: pytest.MonkeyPatch, cortex: _Phase2StubCortex | None = None
) -> None:
    stub = cortex or _Phase2StubCortex()

    class _Reader:
        def entity_get(self, entity_id: str, **kwargs: Any) -> dict[str, Any]:  # noqa: ANN003
            return stub.entity_get(entity_id, **kwargs)

    monkeypatch.setattr(
        "systems.frontier_consult.route.StargateCortexReader",
        _Reader,
    )


def _packet_with_hash(tmp_path: Path, rel: str, source_ref: str) -> str:
    """Write a packet whose frontmatter hash matches a fresh normalize() call."""
    from implement_admission.materialize import materialize
    from implement_admission.normalize import normalize
    from implement_admission.spec import implement_spec_hash

    cortex = _Phase2StubCortex()
    spec = normalize(source_ref, cortex=cortex, workspaces_root=tmp_path)
    out_dir = tmp_path / "tmp" / "reviews"
    mp = materialize(spec, out_dir=out_dir)
    dest = tmp_path / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(Path(mp.path).read_text(encoding="utf-8"), encoding="utf-8")
    return spec.provenance.implement_spec_hash or implement_spec_hash(spec)


def test_p2_handoff_input_underspecified(
    monkeypatch: pytest.MonkeyPatch,
    _handoff_app: FastAPI,
) -> None:
    monkeypatch.setenv("ALLOW_UNSET_AGENT_BUS_TOKEN", "true")
    client = TestClient(_handoff_app, raise_server_exceptions=False)
    resp = client.post(
        "/api/v1/team/handoff",
        json={
            "op": "handoff",
            "role": "cursor-implement",
            "subject": _GOOD_SUBJECT,
        },
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "handoff_input_underspecified"


def test_p2_source_ref_admits_and_creates_thread(
    monkeypatch: pytest.MonkeyPatch,
    _handoff_app: FastAPI,
) -> None:
    monkeypatch.setenv("ALLOW_UNSET_AGENT_BUS_TOKEN", "true")
    _patch_phase2_reader(monkeypatch)
    _patch_bus(monkeypatch, _make_bus_transport(thread_id="bus-p2-admit"))

    client = TestClient(_handoff_app, raise_server_exceptions=False)
    resp = client.post(
        "/api/v1/team/handoff",
        json={
            "op": "handoff",
            "role": "cursor-implement",
            "source_ref": "todo:relay-bounded-single",
            "subject": _GOOD_SUBJECT,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["thread_id"] == "bus-p2-admit"
    assert body["source_ref"] == "todo:relay-bounded-single"
    assert body["implement_spec_hash"]


def test_p2_gated_source_ref_no_thread(
    monkeypatch: pytest.MonkeyPatch,
    _handoff_app: FastAPI,
) -> None:
    monkeypatch.setenv("ALLOW_UNSET_AGENT_BUS_TOKEN", "true")
    _patch_phase2_reader(monkeypatch)

    client = TestClient(_handoff_app, raise_server_exceptions=False)
    resp = client.post(
        "/api/v1/team/handoff",
        json={
            "op": "handoff",
            "role": "cursor-implement",
            "source_ref": "agent-bus:1351",
            "subject": _GOOD_SUBJECT,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "gated"
    assert body["thread_id"] is None
    assert body["gated_reason"]
    assert body["source_ref"] == "agent-bus:1351"


def test_p2_decision_not_asserted_on_source_ref_path(
    monkeypatch: pytest.MonkeyPatch,
    _handoff_app: FastAPI,
) -> None:
    monkeypatch.setenv("ALLOW_UNSET_AGENT_BUS_TOKEN", "true")
    _patch_phase2_reader(monkeypatch, _Phase2BelievedOnlyCortex())

    client = TestClient(_handoff_app, raise_server_exceptions=False)
    resp = client.post(
        "/api/v1/team/handoff",
        json={
            "op": "handoff",
            "role": "cursor-implement",
            "source_ref": "todo:relay-bounded-single",
            "subject": _GOOD_SUBJECT,
        },
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "decision_not_asserted"


def test_p2_legacy_packet_path_unaffected_without_source_ref(
    monkeypatch: pytest.MonkeyPatch,
    _handoff_app: FastAPI,
) -> None:
    """packet_path-only handoff does not require decision lookup."""
    monkeypatch.setenv("ALLOW_UNSET_AGENT_BUS_TOKEN", "true")
    _patch_bus(monkeypatch, _make_bus_transport(thread_id="bus-p2-legacy"))

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
    assert resp.json()["thread_id"] == "bus-p2-legacy"


def test_p2_both_present_hash_match_admits(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("ALLOW_UNSET_AGENT_BUS_TOKEN", "true")
    rel = "universal-llm-gateway/tmp/reviews/hash-match.md"
    source_ref = "todo:relay-bounded-single"
    _packet_with_hash(tmp_path, rel, source_ref)
    _patch_phase2_reader(monkeypatch)
    _patch_bus(monkeypatch, _make_bus_transport(thread_id="bus-p2-hash"))

    mock_proxy = MagicMock()
    mock_proxy.event_bus = None
    fake_deps = types.ModuleType("systems.proxy.dependencies")
    fake_deps.get_proxy = lambda: mock_proxy  # type: ignore[attr-defined]
    if "systems.proxy" not in sys.modules:
        proxy_pkg = types.ModuleType("systems.proxy")
        monkeypatch.setitem(sys.modules, "systems.proxy", proxy_pkg)
    monkeypatch.setitem(sys.modules, "systems.proxy.dependencies", fake_deps)

    app = FastAPI()
    app.include_router(team_router)
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post(
        "/api/v1/team/handoff",
        json={
            "op": "handoff",
            "role": "cursor-implement",
            "source_ref": source_ref,
            "packet_path": rel,
            "subject": _GOOD_SUBJECT,
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["thread_id"] == "bus-p2-hash"


def test_p2_both_present_hash_mismatch_rejected(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("ALLOW_UNSET_AGENT_BUS_TOKEN", "true")
    rel = "universal-llm-gateway/tmp/reviews/hash-mismatch.md"
    _write_packet(
        tmp_path,
        rel,
        "---\nimplement_spec_hash: deadbeef\n---\n" + _CONFORMANT_PACKET,
    )
    _patch_phase2_reader(monkeypatch)

    mock_proxy = MagicMock()
    mock_proxy.event_bus = None
    fake_deps = types.ModuleType("systems.proxy.dependencies")
    fake_deps.get_proxy = lambda: mock_proxy  # type: ignore[attr-defined]
    if "systems.proxy" not in sys.modules:
        proxy_pkg = types.ModuleType("systems.proxy")
        monkeypatch.setitem(sys.modules, "systems.proxy", proxy_pkg)
    monkeypatch.setitem(sys.modules, "systems.proxy.dependencies", fake_deps)

    app = FastAPI()
    app.include_router(team_router)
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post(
        "/api/v1/team/handoff",
        json={
            "op": "handoff",
            "role": "cursor-implement",
            "source_ref": "todo:relay-bounded-single",
            "packet_path": rel,
            "subject": _GOOD_SUBJECT,
        },
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "implement_spec_hash_mismatch"


def test_p2_materialized_dual_root_projects_parent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Materialized packet resolves when PROJECT_ROOT is the projects parent."""
    monkeypatch.setenv("PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("ALLOW_UNSET_AGENT_BUS_TOKEN", "true")
    _patch_phase2_reader(monkeypatch)
    _patch_bus(monkeypatch, _make_bus_transport(thread_id="bus-p2-dual"))

    mock_proxy = MagicMock()
    mock_proxy.event_bus = None
    fake_deps = types.ModuleType("systems.proxy.dependencies")
    fake_deps.get_proxy = lambda: mock_proxy  # type: ignore[attr-defined]
    if "systems.proxy" not in sys.modules:
        proxy_pkg = types.ModuleType("systems.proxy")
        monkeypatch.setitem(sys.modules, "systems.proxy", proxy_pkg)
    monkeypatch.setitem(sys.modules, "systems.proxy.dependencies", fake_deps)

    app = FastAPI()
    app.include_router(team_router)
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post(
        "/api/v1/team/handoff",
        json={
            "op": "handoff",
            "role": "cursor-implement",
            "source_ref": "todo:relay-bounded-single",
            "subject": _GOOD_SUBJECT,
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["thread_id"] == "bus-p2-dual"


def test_p2_materialized_dual_root_repo(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Materialized packet resolves when PROJECT_ROOT is the ULG repo root."""
    repo_root = tmp_path / "universal-llm-gateway"
    repo_root.mkdir()
    monkeypatch.setenv("PROJECT_ROOT", str(repo_root))
    monkeypatch.setenv("ALLOW_UNSET_AGENT_BUS_TOKEN", "true")
    _patch_phase2_reader(monkeypatch)
    _patch_bus(monkeypatch, _make_bus_transport(thread_id="bus-p2-repo"))

    mock_proxy = MagicMock()
    mock_proxy.event_bus = None
    fake_deps = types.ModuleType("systems.proxy.dependencies")
    fake_deps.get_proxy = lambda: mock_proxy  # type: ignore[attr-defined]
    if "systems.proxy" not in sys.modules:
        proxy_pkg = types.ModuleType("systems.proxy")
        monkeypatch.setitem(sys.modules, "systems.proxy", proxy_pkg)
    monkeypatch.setitem(sys.modules, "systems.proxy.dependencies", fake_deps)

    app = FastAPI()
    app.include_router(team_router)
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post(
        "/api/v1/team/handoff",
        json={
            "op": "handoff",
            "role": "cursor-implement",
            "source_ref": "todo:relay-bounded-single",
            "subject": _GOOD_SUBJECT,
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["thread_id"] == "bus-p2-repo"


# ---------------------------------------------------------------------------
# Phase 2 — unified implement admission live wire
# ---------------------------------------------------------------------------


def _noop_decision(**kwargs: object) -> None:  # noqa: ARG001
    return None


def test_phase2_underspecified_neither_field(
    monkeypatch: pytest.MonkeyPatch,
    _handoff_app: FastAPI,
) -> None:
    client = TestClient(_handoff_app, raise_server_exceptions=False)
    resp = client.post(
        "/api/v1/team/handoff",
        json={"op": "handoff", "role": "web-consult", "subject": _GOOD_SUBJECT},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "handoff_input_underspecified"


def test_phase2_source_ref_admits(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    _handoff_app: FastAPI,
) -> None:
    materialized_rel = "universal-llm-gateway/tmp/implement-admission/materialized/x.md"
    _write_packet(tmp_path, materialized_rel, _CONFORMANT_PACKET)
    monkeypatch.setenv("ALLOW_UNSET_AGENT_BUS_TOKEN", "true")
    monkeypatch.setattr(
        "systems.frontier_consult.route.require_decision_asserted",
        _noop_decision,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.route.resolve_source_ref_to_packet",
        lambda source_ref, **kwargs: BridgeResult(
            gated=False,
            source_ref=source_ref,
            packet_path=materialized_rel,
            implement_spec_hash="abc123",
        ),
    )
    _patch_bus(monkeypatch, _make_bus_transport(thread_id="bus-phase2"))

    client = TestClient(_handoff_app, raise_server_exceptions=False)
    resp = client.post(
        "/api/v1/team/handoff",
        json={
            "op": "handoff",
            "role": "cursor-implement",
            "source_ref": "todo:unified-admission-phase2-implement",
            "subject": _GOOD_SUBJECT,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["thread_id"] == "bus-phase2"
    assert body["source_ref"] == "todo:unified-admission-phase2-implement"
    assert body["implement_spec_hash"] == "abc123"


def test_phase2_gated_no_thread(
    monkeypatch: pytest.MonkeyPatch,
    _handoff_app: FastAPI,
) -> None:
    monkeypatch.setattr(
        "systems.frontier_consult.route.require_decision_asserted",
        _noop_decision,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.route.resolve_source_ref_to_packet",
        lambda source_ref, **kwargs: BridgeResult(
            gated=True,
            gated_reason="agent-bus thread ambiguous",
            source_ref=source_ref,
        ),
    )

    client = TestClient(_handoff_app, raise_server_exceptions=False)
    resp = client.post(
        "/api/v1/team/handoff",
        json={
            "op": "handoff",
            "role": "cursor-implement",
            "source_ref": "agent-bus:999",
            "subject": _GOOD_SUBJECT,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "gated"
    assert body["thread_id"] is None
    assert body["gated_reason"]


def test_phase2_decision_not_asserted(
    monkeypatch: pytest.MonkeyPatch,
    _handoff_app: FastAPI,
) -> None:
    from implement_admission.preflight import DecisionNotAssertedError

    def _fail(**kwargs: object) -> None:  # noqa: ARG001
        raise DecisionNotAssertedError()

    monkeypatch.setattr(
        "systems.frontier_consult.route.require_decision_asserted",
        _fail,
    )

    client = TestClient(_handoff_app, raise_server_exceptions=False)
    resp = client.post(
        "/api/v1/team/handoff",
        json={
            "op": "handoff",
            "role": "cursor-implement",
            "source_ref": "todo:unified-admission-phase2-implement",
            "subject": _GOOD_SUBJECT,
        },
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "decision_not_asserted"


def test_phase2_legacy_packet_path_unaffected(
    monkeypatch: pytest.MonkeyPatch,
    _handoff_app: FastAPI,
) -> None:
    """packet_path-only handoff does not invoke the decision gate."""
    monkeypatch.setenv("ALLOW_UNSET_AGENT_BUS_TOKEN", "true")

    def _fail_decision(**kwargs: object) -> None:  # noqa: ARG001
        raise AssertionError("decision gate must not run on legacy path")

    monkeypatch.setattr(
        "systems.frontier_consult.route.require_decision_asserted",
        _fail_decision,
    )
    _patch_bus(monkeypatch, _make_bus_transport(thread_id="bus-legacy"))

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
    assert resp.json()["thread_id"] == "bus-legacy"


def test_phase2_hash_mismatch(
    monkeypatch: pytest.MonkeyPatch,
    _handoff_app: FastAPI,
) -> None:
    monkeypatch.setattr(
        "systems.frontier_consult.route.require_decision_asserted",
        _noop_decision,
    )

    def _hash_fail(**kwargs: object) -> str:
        raise FrontierEndpointError(
            request_id="req-hash",
            field="source_ref",
            reason="mismatch",
            status_code=422,
            code="implement_spec_hash_mismatch",
        )

    monkeypatch.setattr(
        "systems.frontier_consult.route.verify_both_present_hash",
        _hash_fail,
    )

    client = TestClient(_handoff_app, raise_server_exceptions=False)
    resp = client.post(
        "/api/v1/team/handoff",
        json={
            "op": "handoff",
            "role": "cursor-implement",
            "source_ref": "todo:slug",
            "packet_path": _GOOD_PACKET,
            "subject": _GOOD_SUBJECT,
        },
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "implement_spec_hash_mismatch"


# ---------------------------------------------------------------------------
# V2 Step 1 — F1 contract derivation + seat admission
# ---------------------------------------------------------------------------

_V2_REL = "universal-llm-gateway/tmp/reviews/v2-step1-packet.md"


class _V2LaneCortex:
    def __init__(self, *, dispatch_lane: str) -> None:
        self._lane = dispatch_lane

    def entity_get(self, entity_id: str, **kwargs: Any) -> dict[str, Any]:  # noqa: ANN003, ARG002
        return {
            "id": entity_id,
            "attributes": {"dispatch_lane": self._lane},
        }


def test_v2_seat_claude_cursor_admits(
    monkeypatch: pytest.MonkeyPatch,
    _handoff_app: FastAPI,
) -> None:
    """seat=claude-cursor admits without roster role slug."""
    monkeypatch.setenv("ALLOW_UNSET_AGENT_BUS_TOKEN", "true")
    _patch_bus(monkeypatch, _make_bus_transport(thread_id="bus-v2-seat"))

    client = TestClient(_handoff_app, raise_server_exceptions=False)
    resp = client.post(
        "/api/v1/team/handoff",
        json={
            "op": "handoff",
            "seat": "claude-cursor",
            "packet_path": _GOOD_PACKET,
            "subject": _GOOD_SUBJECT,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["to_agent"] == "claude-cursor"
    assert body["handoff_contract"] == "consult"
    assert body["handoff_contract_source"] == "default"


def test_v2_f1_acceptance_shape_does_not_set_contract(tmp_path: Path) -> None:
    """Packet with acceptance criteria but consult lane → consult, not implement."""
    _write_packet(tmp_path, _V2_REL, _CONFORMANT_PACKET)
    cortex = _V2LaneCortex(dispatch_lane="web-implement-packet")
    contract, source = derive_contract(
        source_ref="todo:team-dispatch-surface-v2",
        packet_path=_V2_REL,
        role=None,
        cortex=cortex,
        workspaces_root=tmp_path,
    )
    assert contract == "consult"
    assert source == "source_ref_dispatch_lane"


def test_v2_web_implement_packet_lane_consult_route(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("ALLOW_UNSET_AGENT_BUS_TOKEN", "true")
    _write_packet(tmp_path, _V2_REL, _CONFORMANT_PACKET)
    _patch_phase2_reader(
        monkeypatch, _V2LaneCortex(dispatch_lane="web-implement-packet")
    )
    monkeypatch.setattr(
        "systems.frontier_consult.route.require_decision_asserted",
        _noop_decision,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.route.resolve_source_ref_to_packet",
        lambda source_ref, **kwargs: BridgeResult(
            gated=False,
            source_ref=source_ref,
            packet_path=_V2_REL,
            implement_spec_hash="abc123",
        ),
    )
    _patch_bus(monkeypatch, _make_bus_transport(thread_id="bus-v2-wip"))

    mock_proxy = MagicMock()
    mock_proxy.event_bus = None
    fake_deps = types.ModuleType("systems.proxy.dependencies")
    fake_deps.get_proxy = lambda: mock_proxy  # type: ignore[attr-defined]
    if "systems.proxy" not in sys.modules:
        proxy_pkg = types.ModuleType("systems.proxy")
        monkeypatch.setitem(sys.modules, "systems.proxy", proxy_pkg)
    monkeypatch.setitem(sys.modules, "systems.proxy.dependencies", fake_deps)

    app = FastAPI()
    app.include_router(team_router)
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post(
        "/api/v1/team/handoff",
        json={
            "op": "handoff",
            "seat": "claude-cursor",
            "source_ref": "todo:team-dispatch-surface-v2",
            "subject": _GOOD_SUBJECT,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["handoff_contract"] == "consult"
    assert body["handoff_contract_source"] == "source_ref_dispatch_lane"


def test_v2_cursor_implement_lane_implement_and_acceptance_lint(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    packet_no_acceptance = _CONFORMANT_PACKET.replace(
        "## Acceptance criteria\n1. It works.", "Just do the work."
    )
    _write_packet(tmp_path, _V2_REL, packet_no_acceptance)
    monkeypatch.setenv("PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("ALLOW_UNSET_AGENT_BUS_TOKEN", "true")
    _patch_phase2_reader(monkeypatch, _V2LaneCortex(dispatch_lane="cursor-implement"))
    monkeypatch.setattr(
        "systems.frontier_consult.route.require_decision_asserted",
        _noop_decision,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.route.resolve_source_ref_to_packet",
        lambda source_ref, **kwargs: BridgeResult(
            gated=False,
            source_ref=source_ref,
            packet_path=_V2_REL,
            implement_spec_hash="abc123",
        ),
    )
    _patch_bus(monkeypatch, _make_bus_transport(thread_id="bus-v2-impl"))

    mock_proxy = MagicMock()
    mock_proxy.event_bus = None
    fake_deps = types.ModuleType("systems.proxy.dependencies")
    fake_deps.get_proxy = lambda: mock_proxy  # type: ignore[attr-defined]
    if "systems.proxy" not in sys.modules:
        proxy_pkg = types.ModuleType("systems.proxy")
        monkeypatch.setitem(sys.modules, "systems.proxy", proxy_pkg)
    monkeypatch.setitem(sys.modules, "systems.proxy.dependencies", fake_deps)

    app = FastAPI()
    app.include_router(team_router)
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post(
        "/api/v1/team/handoff",
        json={
            "op": "handoff",
            "seat": "claude-cursor",
            "source_ref": "todo:team-dispatch-surface-v2",
            "subject": _GOOD_SUBJECT,
        },
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "handoff_packet_missing_acceptance"


def test_v2_resolve_handoff_seat_alias() -> None:
    to_agent, _f, platform, resolved = resolve_handoff_seat(
        seat="claude-web",
        request_id="req-v2-seat",
    )
    assert to_agent == "claude-web"
    assert resolved == "claude-web"
    assert platform == "web"


# ---------------------------------------------------------------------------
# Step 6 — gate_a2, widened gate_a, materialization_mode
# ---------------------------------------------------------------------------

_IMPLEMENT_PACKET_FM = """\
---
source_ref: todo:traced
---
<scope>Goal: x.</scope>
<invariants>x</invariants>
<task_guidance>## Acceptance criteria
1. It works.</task_guidance>
<corpus>the artifact</corpus>
<mcp_capabilities>You have MCP.</mcp_capabilities>
<output_format>Reply.</output_format>
"""


def test_gate_a2_consult_exempt(tmp_path: Path) -> None:
    _write_packet(tmp_path, _PV_REL, _CONFORMANT_PACKET)
    validate_packet(
        request_id="req-a2-consult",
        packet_path=_PV_REL,
        to_agent="claude-cursor",
        handoff_contract="consult",
        workspaces_root=tmp_path,
    )


def test_gate_a2_warn_missing_frontmatter(tmp_path: Path) -> None:
    _write_packet(tmp_path, _PV_REL, _CONFORMANT_PACKET)
    with patch.dict(os.environ, {"UA_DRIFT_GATE_A2": "warn"}, clear=False):
        from implement_admission.drift_gates import clear_gate_state_cache

        clear_gate_state_cache()
        result = validate_packet(
            request_id="req-a2-warn",
            packet_path=_PV_REL,
            to_agent="claude-cursor",
            handoff_contract="implement",
            workspaces_root=tmp_path,
            source_ref="todo:foo",
        )
    assert any("drift_gate.a2.miss" in w for w in result.warnings)


def test_gate_a2_enforce_missing_frontmatter(tmp_path: Path) -> None:
    _write_packet(tmp_path, _PV_REL, _CONFORMANT_PACKET)
    with patch.dict(os.environ, {"UA_DRIFT_GATE_A2": "enforce"}, clear=False):
        from implement_admission.drift_gates import clear_gate_state_cache

        clear_gate_state_cache()
        with pytest.raises(FrontierEndpointError) as exc_info:
            validate_packet(
                request_id="req-a2-enforce",
                packet_path=_PV_REL,
                to_agent="claude-cursor",
                handoff_contract="implement",
                workspaces_root=tmp_path,
                source_ref="todo:foo",
            )
    assert exc_info.value.code == "handoff_packet_missing_source_ref"


def test_gate_a_frontmatter_only_admits(tmp_path: Path) -> None:
    _write_packet(tmp_path, _PV_REL, _IMPLEMENT_PACKET_FM)
    with patch.dict(os.environ, {"UA_DRIFT_GATE_A": "enforce"}, clear=False):
        from implement_admission.drift_gates import clear_gate_state_cache

        clear_gate_state_cache()
        result = validate_packet(
            request_id="req-a-fm-only",
            packet_path=_PV_REL,
            to_agent="claude-cursor",
            handoff_contract="implement",
            workspaces_root=tmp_path,
            source_ref=None,
        )
    assert result.frontmatter_source_ref == "todo:traced"


def test_gate_a2_malformed_ref_treated_absent(tmp_path: Path) -> None:
    bad_fm = _CONFORMANT_PACKET.replace(
        "<scope>",
        "---\nsource_ref: not a real ref!!\n---\n<scope>",
        1,
    )
    _write_packet(tmp_path, _PV_REL, bad_fm)
    with patch.dict(os.environ, {"UA_DRIFT_GATE_A2": "enforce"}, clear=False):
        from implement_admission.drift_gates import clear_gate_state_cache

        clear_gate_state_cache()
        with pytest.raises(FrontierEndpointError) as exc_info:
            validate_packet(
                request_id="req-a2-bad",
                packet_path=_PV_REL,
                to_agent="claude-cursor",
                handoff_contract="implement",
                workspaces_root=tmp_path,
                source_ref="todo:foo",
            )
    assert exc_info.value.code == "handoff_packet_missing_source_ref"

