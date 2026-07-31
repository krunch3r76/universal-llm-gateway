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
from implement_admission.drift_gates import DriftGateState
from pydantic import ValidationError

from .admission import (
    FrontierEndpointError,
    enforce_team_dispatch_generate_admit,
    resolve_cursor_sdk_handoff_seat,
    resolve_handoff_contract,
    resolve_handoff_seat,
    resolve_handoff_target,
    resolve_web_handoff_seat,
)
from .contract_derivation import derive_contract

_WORKER_ADMIT_OK: tuple[bool, dict[str, Any]] = (
    True,
    {"status_code": 200, "ticket": {}},
)
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
_ARCH_SKILL_REFS = (
    "- Use the `architecture-invariants` skill "
    "(canonical slug — seat self-fetches; ¬ fs-read skill body)\n"
    "- Use the `ulg-architecture` skill "
    "(canonical slug — seat self-fetches; ¬ fs-read skill body)\n"
    "- Use the `docstring-quality` skill "
    "(canonical slug — seat self-fetches; ¬ fs-read skill body)"
)

_DENSIFY_FLOOR_REFS = (
    "- Use the `consult-routing` skill "
    "(canonical slug — seat self-fetches; ¬ fs-read skill body)\n"
    "- Use the `lead-seat-boot` skill "
    "(canonical slug — seat self-fetches; ¬ fs-read skill body)\n"
    "- Use the `handoff-packet-authoring` skill "
    "(canonical slug — seat self-fetches; ¬ fs-read skill body)"
)

_CONFORMANT_PACKET = f"""\
<scope>Goal: x. Selection mode: targeted.</scope>
<invariants>[scope] every changed line traces to task.
{_ARCH_SKILL_REFS}
{_DENSIFY_FLOOR_REFS}</invariants>
<task_guidance>## Acceptance criteria
1. It works.</task_guidance>
<corpus>the artifact</corpus>
<mcp_capabilities>LIFE/CORTEX MCP: ON
CODE/VORTEX MCP: OFF — no workspaces or code-only tools.</mcp_capabilities>
<output_format>Reply on thread.</output_format>
"""

_CONSULT_ONLY_PACKET = f"""\
<scope>Goal: x. Selection mode: targeted.</scope>
<invariants>[scope] every changed line traces to task.
{_ARCH_SKILL_REFS}
{_DENSIFY_FLOOR_REFS}</invariants>
<task_guidance>Review questions and risks.</task_guidance>
<corpus>the artifact</corpus>
<mcp_capabilities>LIFE/CORTEX MCP: ON
CODE/VORTEX MCP: OFF — no workspaces or code-only tools.</mcp_capabilities>
<output_format>Reply on thread.</output_format>
"""

# 1296-style improvised packet: numbered sections, missing
# <corpus> + <mcp_capabilities>.
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


def _route_app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> FastAPI:
    """FastAPI app with team_router and mocked get_proxy."""
    monkeypatch.setenv("PROJECT_ROOT", str(tmp_path))
    _patch_phase2_reader(monkeypatch)
    _patch_gates_warn(monkeypatch)
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
    return app


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
    _patch_phase2_reader(monkeypatch)
    _patch_gates_warn(monkeypatch)

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
    assert "cortex://ephemeral/handoffs/" in body_text
    assert "smoke-packet" in body_text


def test_h1a_route_web_consult_admits_without_arch_skillrefs(
    monkeypatch: pytest.MonkeyPatch,
    _handoff_app: FastAPI,
    tmp_path: Path,
) -> None:
    """role=web-consult → claude-web; arch skill-refs omitted when densify floor met."""
    monkeypatch.setenv("ALLOW_UNSET_AGENT_BUS_TOKEN", "true")
    _patch_bus(monkeypatch, _make_bus_transport(thread_id="bus-thread-web"))
    _write_packet(
        tmp_path, _GOOD_PACKET, _CONFORMANT_PACKET.replace(_ARCH_SKILL_REFS, "")
    )

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


def test_h1a_route_web_consult_push_reminder_mentions_web_push(
    monkeypatch: pytest.MonkeyPatch,
    _handoff_app: FastAPI,
) -> None:
    """role=web-consult → claude-web; push_reminder tells operator to push."""
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
    assert body["to_agent"] == "web-anthropic"
    assert body["handoff_contract"] == "consult"
    assert "push" in body["push_reminder"].lower()
    assert "web claude" in body["push_reminder"].lower()
    assert body["poll_hint"]["arguments"]["from_agent"] == "web-anthropic"


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
    assert body["to_agent"] == "web-anthropic"
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
# H1b — cursor-consult / claude-cursor seat (manual_handoff)
# ---------------------------------------------------------------------------


def test_h1b_cursor_consult_resolves_claude_cursor() -> None:
    to_agent, _family, platform = resolve_web_handoff_seat(
        "cursor-consult", request_id="req-c1"
    )
    assert to_agent == "cursor"
    assert platform == "cursor"


def test_h1b_claude_cursor_seat_slug_profile_resolves() -> None:
    """Seat slug resolves at profile layer; handoff route rejects it (roster only)."""
    to_agent, _family, platform = resolve_web_handoff_seat(
        "claude-cursor", request_id="req-c2"
    )
    assert to_agent == "cursor"
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
    assert body["to_agent"] == "cursor"
    assert "cursor" in body["push_reminder"].lower()


# ---------------------------------------------------------------------------
# H1c — cursor-implement role (claude/cursor manual seat, implement contract)
# ---------------------------------------------------------------------------


def test_h1c_cursor_implement_resolves_claude_cursor() -> None:
    """cursor-implement: admitted handoff, resolves to claude-cursor."""
    to_agent, _family, platform = resolve_web_handoff_seat(
        "cursor-implement", request_id="req-i1"
    )
    assert to_agent == "cursor"
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
    assert body["to_agent"] == "cursor"
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
    assert to_agent == "web-anthropic"
    assert platform == "web"


def test_h1d_web_implement_target_resolves() -> None:
    to_agent, _f, platform, resolved = resolve_handoff_target(
        role="web-implement",
        request_id="req-wi-target",
    )
    assert to_agent == "web-anthropic"
    assert resolved == "web-anthropic"
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
    assert body["to_agent"] == "web-anthropic"
    assert body["resolved_handoff_seat"] == "web-anthropic"
    assert body["handoff_contract"] == "implement"
    assert body["handoff_contract_source"] == "role_default"
    assert "push" in body["push_reminder"].lower()
    assert body["poll_hint"]["arguments"]["from_agent"] == "web-anthropic"


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
# H2 — api_dispatchable role → 422 handoff_requires_web_seat
# ---------------------------------------------------------------------------


def test_h2_dispatchable_role_rejected() -> None:
    """reviewer → gpt/api is api_dispatchable → admission fails."""
    with pytest.raises(FrontierEndpointError) as exc_info:
        resolve_web_handoff_seat("reviewer", request_id="req-h2")
    err = exc_info.value
    assert err.status_code == 422
    assert err.field == "role"
    assert err.code == "handoff_requires_web_seat"
    assert "manual_handoff" in err.reason


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
        to_agent="claude-web",
    )
    assert "cortex://ephemeral/handoffs/" in result
    assert "smoke-packet" in result
    assert _GOOD_SUBJECT in result
    assert "LIFE/CORTEX MCP ON" in result
    assert "CODE/VORTEX MCP OFF" in result
    lines = result.splitlines()
    assert len(lines) <= 25


def test_h5b_consult_pointer_omits_arch_read_on_override() -> None:
    result = build_pointer_body(
        request_id="req-h5b",
        packet_path=_GOOD_PACKET,
        subject=_GOOD_SUBJECT,
        pointer_body="custom consult pointer",
        handoff_contract="consult",
    )
    assert result == "custom consult pointer"
    assert "architecture-invariants.md" not in result


def test_h5c_implement_pointer_omits_consult_arch_read() -> None:
    result = build_pointer_body(
        request_id="req-h5c",
        packet_path=_GOOD_PACKET,
        subject=_GOOD_SUBJECT,
        pointer_body=None,
        handoff_contract="implement",
    )
    assert "architecture-invariants.md" not in result


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
    assert body["resolved_handoff_seat"] == "web-anthropic"


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
    assert body["resolved_model"] == "cursor"
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
    assert body["resolved_model"] == "cursor"
    assert body["handoff_contract"] == "consult"
    assert body["handoff_contract_source"] == "role_default"


def test_hc5d_cursor_implement_resolves() -> None:
    to_agent, _f, platform, resolved = resolve_handoff_target(
        role="cursor-implement",
        request_id="req-agree",
    )
    assert to_agent == "cursor"
    assert resolved == "cursor"
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
    """Consult (web-consult) does not require acceptance in task_guidance."""
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
    _write_packet(
        repo_root,
        rel,
        _packet_with_source_ref_frontmatter("todo:pv", _CONFORMANT_PACKET),
    )
    with patch(
        "implement_admission.drift_gates.gate_state",
        lambda gate_id: DriftGateState.WARN,
    ):
        from implement_admission.drift_gates import clear_gate_state_cache

        clear_gate_state_cache()
        validate_packet(
            request_id="req-pv2c",
            packet_path=rel,
            to_agent="claude-cursor",
            handoff_contract="implement",
            workspaces_root=repo_root,
            source_ref="todo:pv",
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
        "<mcp_capabilities>LIFE/CORTEX MCP: ON\n"
        "CODE/VORTEX MCP: OFF — no workspaces or code-only tools.</mcp_capabilities>\n",
        "",
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
        "<mcp_capabilities>LIFE/CORTEX MCP: ON\n"
        "CODE/VORTEX MCP: OFF — no workspaces or code-only tools.</mcp_capabilities>\n",
        "",
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


def test_pv_web_omits_arch_skillrefs_when_densify_floor_met(tmp_path: Path) -> None:
    """claude-web skips arch skill-ref gate when densify floor is satisfied."""
    packet = _CONFORMANT_PACKET.replace(_ARCH_SKILL_REFS, "")
    _write_packet(tmp_path, _PV_REL, packet)
    validate_packet(
        request_id="req-pv-arch1",
        packet_path=_PV_REL,
        to_agent="claude-web",
        handoff_contract="consult",
        workspaces_root=tmp_path,
    )


def test_pv_arch_skillrefs_partial_rejected_for_cursor(tmp_path: Path) -> None:
    """One ref present, the other missing → reject citing only the missing one."""
    packet = _CONFORMANT_PACKET.replace(
        "- Use the `ulg-architecture` skill "
        "(canonical slug — seat self-fetches; ¬ fs-read skill body)",
        "",
    )
    _write_packet(tmp_path, _PV_REL, packet)
    with pytest.raises(FrontierEndpointError) as exc_info:
        validate_packet(
            request_id="req-pv-arch2",
            packet_path=_PV_REL,
            to_agent="claude-cursor",
            handoff_contract="consult",
            workspaces_root=tmp_path,
        )
    err = exc_info.value
    assert err.code == "handoff_packet_missing_arch_skillrefs"
    assert err.details is not None
    assert err.details["missing_refs"] == ["ulg-architecture"]
    assert "ulg-architecture" in err.reason


def test_pv_arch_skillrefs_anywhere_in_packet_admitted(tmp_path: Path) -> None:
    """Refs satisfy the gate from any block (matches materializer placement)."""
    packet = _CONFORMANT_PACKET.replace(
        _ARCH_SKILL_REFS, "[scope] no skill refs in invariants"
    ).replace(
        "<mcp_capabilities>You have MCP. Cite tool calls.",
        "<mcp_capabilities>You have MCP. Cite tool calls. "
        "Use the `architecture-invariants` skill "
        "Use the `ulg-architecture` skill "
        "Use the `docstring-quality` skill",
    )
    _write_packet(tmp_path, _PV_REL, packet)
    validate_packet(
        request_id="req-pv-arch3",
        packet_path=_PV_REL,
        to_agent="claude-web",
        handoff_contract="consult",
        workspaces_root=tmp_path,
    )


def test_pv_arch_skillrefs_display_name_rejected_with_rewrite_hint(
    tmp_path: Path,
) -> None:
    """Display-name skill-ref paths reject with exact expected_refs + a precise
    rewrite hint (friction 16958) instead of a bare slug-only 422.
    """
    display_refs = (
        "- fs(cortex, agent-skills/Architecture Invariants — Universal Layer.md)\n"
        "- fs(cortex, agent-skills/ULG Architecture — universal-llm-gateway Layer.md)"
    )
    packet = _CONFORMANT_PACKET.replace(_ARCH_SKILL_REFS, display_refs)
    _write_packet(tmp_path, _PV_REL, packet)
    with pytest.raises(FrontierEndpointError) as exc_info:
        validate_packet(
            request_id="req-pv-arch-display",
            packet_path=_PV_REL,
            to_agent="claude-cursor",
            handoff_contract="consult",
            workspaces_root=tmp_path,
        )
    err = exc_info.value
    assert err.code == "handoff_packet_missing_arch_skillrefs"
    # Structured exact strings ride in the 422 body.
    assert err.details is not None
    assert err.details["expected_refs"] == [
        "architecture-invariants",
        "ulg-architecture",
        "docstring-quality",
    ]
    assert err.to_dict()["details"]["expected_refs"]
    # Near-miss recognized → precise rewrite guidance, not a re-discovery loop.
    assert "non-canonical" in err.reason
    assert "architecture-invariants" in err.reason
    assert "ulg-architecture" in err.reason
    assert "docstring-quality" in err.reason


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
    _write_packet(
        tmp_path,
        _PV_REL,
        _packet_with_source_ref_frontmatter("todo:pv", _CONFORMANT_PACKET),
    )
    with patch(
        "implement_admission.drift_gates.gate_state",
        lambda gate_id: DriftGateState.WARN,
    ):
        from implement_admission.drift_gates import clear_gate_state_cache

        clear_gate_state_cache()
        validate_packet(
            request_id="req-pv6",
            packet_path=_PV_REL,
            to_agent="claude-cursor",
            handoff_contract="implement",
            workspaces_root=tmp_path,
            source_ref="todo:pv",
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
    _write_packet(
        tmp_path,
        _PV_REL,
        _packet_with_source_ref_frontmatter("todo:foo", _CONFORMANT_PACKET),
    )
    with patch(
        "implement_admission.drift_gates.gate_state",
        lambda gate_id: DriftGateState.WARN,
    ):
        from implement_admission.drift_gates import clear_gate_state_cache

        clear_gate_state_cache()
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
    with patch(
        "implement_admission.drift_gates.gate_state",
        side_effect=lambda gate_id: {
            "a": DriftGateState.WARN,
            "a2": DriftGateState.OFF,
        }.get(gate_id, DriftGateState.WARN),
    ):
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

    def assertion_state(self, entity_id: str, **kwargs: Any) -> dict[str, Any]:  # noqa: ANN003, ARG002
        if entity_id == "decision:unified-implement-admission":
            return {
                "entity_id": entity_id,
                "ratified": True,
                "confirmed_count": 1,
                "latest_confirmed_assertion_id": 1,
            }
        return {
            "entity_id": entity_id,
            "ratified": False,
            "confirmed_count": 0,
            "latest_confirmed_assertion_id": None,
        }

    def entity_get(self, entity_id: str, **kwargs: Any) -> dict[str, Any]:  # noqa: ANN003, ARG002
        if entity_id.startswith("agent_skill:"):
            slug = entity_id.removeprefix("agent_skill:")
            return {
                "id": entity_id,
                "attributes": {
                    "source_uri": (
                        "workspaces://universal-llm-gateway/"
                        f".cursor/skills/{slug}/SKILL.md"
                    )
                },
            }
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
            "density_triage": "mechanical",
            "acceptance_criteria": ["AC1", "AC2"],
            "files_expected": ["a.py", "b.py"],
        }
        return {"id": entity_id, "name": entity_id, "attributes": attrs}


class _Phase2BelievedOnlyCortex(_Phase2StubCortex):
    def assertion_state(self, entity_id: str, **kwargs: Any) -> dict[str, Any]:  # noqa: ANN003, ARG002
        if entity_id == "decision:unified-implement-admission":
            return {
                "entity_id": entity_id,
                "ratified": False,
                "confirmed_count": 0,
                "latest_confirmed_assertion_id": None,
            }
        return super().assertion_state(entity_id, **kwargs)

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


def _patch_gates_warn(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hermetic default — live config entity may have gates at enforce."""
    monkeypatch.setattr(
        "implement_admission.drift_gates.gate_state",
        lambda gate_id: DriftGateState.WARN,
    )
    from implement_admission.drift_gates import clear_gate_state_cache

    clear_gate_state_cache()


def _patch_phase2_reader(
    monkeypatch: pytest.MonkeyPatch, cortex: _Phase2StubCortex | None = None
) -> None:
    stub = cortex or _Phase2StubCortex()

    class _Reader:
        def assertion_state(self, entity_id: str, **kwargs: Any) -> dict[str, Any]:  # noqa: ANN003
            return stub.assertion_state(entity_id, **kwargs)

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


def test_p2_packet_path_only_rejects_without_decision(
    monkeypatch: pytest.MonkeyPatch,
    _handoff_app: FastAPI,
) -> None:
    """packet_path-only lane enforces require_decision_asserted (S3)."""
    monkeypatch.setenv("ALLOW_UNSET_AGENT_BUS_TOKEN", "true")
    _patch_phase2_reader(monkeypatch, _Phase2BelievedOnlyCortex())

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
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "decision_not_asserted"


def test_p2_packet_path_only_admits_with_decision(
    monkeypatch: pytest.MonkeyPatch,
    _handoff_app: FastAPI,
) -> None:
    """packet_path-only handoff admits when decision is confirmed."""
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
    body = resp.json()
    assert body["thread_id"] == "bus-p2-legacy"
    assert body["materialization_mode"] == "hand_authored"


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
    body = resp.json()
    assert body["thread_id"] == "bus-p2-hash"
    assert body["materialization_mode"] == "hand_authored_traced"


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


def test_p2_both_present_hash_absent_stamps_and_admits(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Both present, frontmatter implement_spec_hash ABSENT → server stamps + admits.

    A non-shell authoring seat cannot run normalize() to precompute the hash;
    an absent stamp is trusted (server recomputes it here) rather than 422'd.
    The 422 is reserved for a genuine mismatch (see the test above).
    """
    monkeypatch.setenv("PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("ALLOW_UNSET_AGENT_BUS_TOKEN", "true")
    rel = "universal-llm-gateway/tmp/reviews/hash-absent.md"
    source_ref = "todo:relay-bounded-single"
    # Conformant six-block packet carrying source_ref frontmatter (gate_a2) but
    # NO implement_spec_hash — the case a web/reasoning seat can actually produce.
    _write_packet(
        tmp_path,
        rel,
        f"---\nsource_ref: {source_ref}\n---\n" + _CONFORMANT_PACKET,
    )
    _patch_phase2_reader(monkeypatch)
    _patch_bus(monkeypatch, _make_bus_transport(thread_id="bus-p2-absent"))

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
    body = resp.json()
    assert body["thread_id"] == "bus-p2-absent"
    assert body["materialization_mode"] == "hand_authored_traced"
    # Server stamped the computed hash into the response.
    assert body["implement_spec_hash"].startswith("sha256:")


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
    body = resp.json()
    assert body["thread_id"] == "bus-p2-dual"
    assert body["materialization_mode"] == "auto"


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


def test_materialization_present_false_surfaces_warning(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    _handoff_app: FastAPI,
) -> None:
    materialized_rel = "universal-llm-gateway/tmp/implement-admission/materialized/x.md"
    _write_packet(tmp_path, materialized_rel, _CONFORMANT_PACKET)
    warning = (
        "materialization.executor_absent: "
        f"{materialized_rel} not visible at executor root /mnt/executor; "
        "use source_ref fallback"
    )
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
            materialization_present=False,
            warnings=[warning],
        ),
    )
    _patch_bus(monkeypatch, _make_bus_transport(thread_id="bus-mat-absent"))

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
    assert body["materialization_present"] is False
    assert warning in body["warnings"]


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


def test_phase2_packet_path_invokes_decision_gate(
    monkeypatch: pytest.MonkeyPatch,
    _handoff_app: FastAPI,
) -> None:
    """packet_path-only handoff runs require_decision_asserted (S3)."""
    monkeypatch.setenv("ALLOW_UNSET_AGENT_BUS_TOKEN", "true")
    decision_calls: list[bool] = []

    def _track_decision(**kwargs: object) -> None:  # noqa: ARG001
        decision_calls.append(True)

    monkeypatch.setattr(
        "systems.frontier_consult.route.require_decision_asserted",
        _track_decision,
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
    assert decision_calls


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

    def assertion_state(self, entity_id: str, **kwargs: Any) -> dict[str, Any]:  # noqa: ANN003, ARG002
        return {
            "entity_id": entity_id,
            "ratified": True,
            "confirmed_count": 1,
            "latest_confirmed_assertion_id": 1,
        }

    def entity_get(self, entity_id: str, **kwargs: Any) -> dict[str, Any]:  # noqa: ANN003, ARG002
        return {
            "id": entity_id,
            "attributes": {"dispatch_lane": self._lane},
        }


def test_v2_seat_claude_cursor_admits(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """seat=claude-cursor admits without roster role slug."""
    monkeypatch.setenv("ALLOW_UNSET_AGENT_BUS_TOKEN", "true")
    _write_packet(tmp_path, _GOOD_PACKET, _CONSULT_ONLY_PACKET)
    _patch_bus(monkeypatch, _make_bus_transport(thread_id="bus-v2-seat"))

    client = TestClient(
        _route_app(monkeypatch, tmp_path), raise_server_exceptions=False
    )
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
    assert body["to_agent"] == "cursor"
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
    assert to_agent == "web-anthropic"
    assert resolved == "web-anthropic"
    assert platform == "web"


# ---------------------------------------------------------------------------
# Step 6 — gate_a2, widened gate_a, materialization_mode
# ---------------------------------------------------------------------------

_IMPLEMENT_PACKET_FM = """\
---
source_ref: todo:traced
---
<scope>Goal: x.</scope>
<invariants>x
- Use the `architecture-invariants` skill (canonical slug — seat self-fetches; ¬ fs-read skill body)
- Use the `ulg-architecture` skill (canonical slug — seat self-fetches; ¬ fs-read skill body)
- Use the `docstring-quality` skill (canonical slug — seat self-fetches; ¬ fs-read skill body)</invariants>
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
    with patch(
        "implement_admission.drift_gates.gate_state",
        side_effect=lambda gate_id: {
            "a2": DriftGateState.WARN,
            "a": DriftGateState.OFF,
        }.get(gate_id, DriftGateState.WARN),
    ):
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
    with patch(
        "implement_admission.drift_gates.gate_state",
        side_effect=lambda gate_id: {
            "a2": DriftGateState.ENFORCE,
            "a": DriftGateState.OFF,
        }.get(gate_id, DriftGateState.WARN),
    ):
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
    with patch(
        "implement_admission.drift_gates.gate_state",
        side_effect=lambda gate_id: {
            "a2": DriftGateState.ENFORCE,
            "a": DriftGateState.OFF,
        }.get(gate_id, DriftGateState.WARN),
    ):
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


def test_materialization_mode_packet_path_only_with_frontmatter(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """packet_path-only with frontmatter source_ref → hand_authored_traced."""
    monkeypatch.setenv("PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("ALLOW_UNSET_AGENT_BUS_TOKEN", "true")
    _write_packet(tmp_path, _PV_REL, _IMPLEMENT_PACKET_FM)
    _patch_bus(monkeypatch, _make_bus_transport(thread_id="bus-mode-traced"))
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
            "packet_path": _PV_REL,
            "subject": _GOOD_SUBJECT,
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["materialization_mode"] == "hand_authored_traced"


# ---------------------------------------------------------------------------
# Phase-2 — team-dispatch handoff DX (D1–D4, thread 1525)
# ---------------------------------------------------------------------------

_DX_REL = "universal-llm-gateway/tmp/reviews/dx-phase2-packet.md"
_DX_REL_STRIPPED = "tmp/reviews/dx-phase2-packet.md"


def test_d4_explicit_contract_param_beats_role_default(tmp_path: Path) -> None:
    _write_packet(tmp_path, _DX_REL, _CONFORMANT_PACKET)
    contract, source = derive_contract(
        explicit_contract="implement",
        source_ref=None,
        packet_path=_DX_REL,
        role="web-consult",
        cortex=_V2LaneCortex(dispatch_lane="web-spec"),
        workspaces_root=tmp_path,
    )
    assert contract == "implement"
    assert source == "explicit_param"


def test_d4_explicit_contract_param_admits_implement_route(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALLOW_UNSET_AGENT_BUS_TOKEN", "true")
    _write_packet(tmp_path, _DX_REL, _IMPLEMENT_PACKET_FM)
    _patch_bus(monkeypatch, _make_bus_transport(thread_id="bus-dx-explicit"))

    client = TestClient(
        _route_app(monkeypatch, tmp_path), raise_server_exceptions=False
    )
    with patch.dict(
        os.environ, {"UA_DRIFT_GATE_A": "off", "UA_DRIFT_GATE_A2": "off"}, clear=False
    ):
        from implement_admission.drift_gates import clear_gate_state_cache

        clear_gate_state_cache()
        resp = client.post(
            "/api/v1/team/handoff",
            json={
                "op": "handoff",
                "role": "cursor-implement",
                "packet_path": _DX_REL,
                "contract": "implement",
                "subject": _GOOD_SUBJECT,
            },
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["handoff_contract"] == "implement"
    assert body["handoff_contract_source"] == "explicit_param"


def test_d4_ambiguous_rejects_acceptance_without_contract(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _write_packet(tmp_path, _DX_REL, _CONFORMANT_PACKET)

    client = TestClient(
        _route_app(monkeypatch, tmp_path), raise_server_exceptions=False
    )
    resp = client.post(
        "/api/v1/team/handoff",
        json={
            "op": "handoff",
            "seat": "claude-cursor",
            "packet_path": _DX_REL,
            "subject": _GOOD_SUBJECT,
        },
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"]["code"] == "handoff_contract_ambiguous"


def test_d4_consult_packet_no_acceptance_admits_default(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALLOW_UNSET_AGENT_BUS_TOKEN", "true")
    _write_packet(tmp_path, _DX_REL, _CONSULT_ONLY_PACKET)
    _patch_bus(monkeypatch, _make_bus_transport(thread_id="bus-dx-consult"))

    client = TestClient(
        _route_app(monkeypatch, tmp_path), raise_server_exceptions=False
    )
    resp = client.post(
        "/api/v1/team/handoff",
        json={
            "op": "handoff",
            "seat": "claude-cursor",
            "packet_path": _DX_REL,
            "subject": _GOOD_SUBJECT,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["handoff_contract"] == "consult"
    assert body["handoff_contract_source"] == "default"


def test_d2_packet_path_prefix_coercion_route(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALLOW_UNSET_AGENT_BUS_TOKEN", "true")
    _write_packet(tmp_path, _DX_REL_STRIPPED, _CONSULT_ONLY_PACKET)
    _patch_bus(monkeypatch, _make_bus_transport(thread_id="bus-dx-prefix"))

    client = TestClient(
        _route_app(monkeypatch, tmp_path), raise_server_exceptions=False
    )
    for packet_path in (_DX_REL, _DX_REL_STRIPPED):
        resp = client.post(
            "/api/v1/team/handoff",
            json={
                "op": "handoff",
                "seat": "claude-cursor",
                "packet_path": packet_path,
                "subject": _GOOD_SUBJECT,
            },
        )
        assert resp.status_code == 200, resp.text


def test_d2_genuinely_missing_path_still_rejects(tmp_path: Path) -> None:
    with pytest.raises(FrontierEndpointError) as exc_info:
        validate_packet(
            request_id="req-dx-missing",
            packet_path="tmp/reviews/no-such-packet.md",
            to_agent="claude-cursor",
            handoff_contract="consult",
            workspaces_root=tmp_path,
        )
    assert exc_info.value.code == "handoff_packet_missing"


# ---------------------------------------------------------------------------
# Phase-2 packet lane — E1a pass-through, E2a′ gate-B drop, S3/S4
# ---------------------------------------------------------------------------

_PACKET_RT_REL = "universal-llm-gateway/tmp/reviews/packet-roundtrip.md"


def _patch_enforce_all_gates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "implement_admission.drift_gates.gate_state",
        lambda gate_id: DriftGateState.ENFORCE,
    )
    from implement_admission.drift_gates import clear_gate_state_cache

    clear_gate_state_cache()


def _packet_with_source_ref_frontmatter(source_ref: str, body: str) -> str:
    return f"---\nsource_ref: {source_ref}\n---\n{body}"


def test_s4_packet_source_ref_passthrough_admits_enforce(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """packet: source_ref admits on first try; enrich-normalized packet is deduped."""
    import re
    from collections import Counter

    from .handoff import _extract_block
    from .handoff_packet_enrich import (
        _DEFAULT_DENSIFY_SLUGS,
        enrich_handoff_packet,
    )

    def _line_references_slug(line: str, slug: str) -> bool:
        lowered = line.lower()
        needle = slug.lower()
        return (
            f"agent_skill:{needle}" in lowered
            or f"agent-skills/{needle}.md" in lowered
            or f"/{needle}.md" in lowered
            or f"`{needle}`" in lowered
        )

    monkeypatch.setenv("PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("ALLOW_UNSET_AGENT_BUS_TOKEN", "true")
    source_ref = f"packet:{_PACKET_RT_REL}"
    _write_packet(
        tmp_path,
        _PACKET_RT_REL,
        _packet_with_source_ref_frontmatter(source_ref, _CONFORMANT_PACKET),
    )
    _patch_phase2_reader(monkeypatch)
    _patch_enforce_all_gates(monkeypatch)
    _patch_bus(monkeypatch, _make_bus_transport(thread_id="bus-packet-rt"))

    client = TestClient(
        _route_app(monkeypatch, tmp_path), raise_server_exceptions=False
    )
    resp = client.post(
        "/api/v1/team/handoff",
        json={
            "op": "handoff",
            "role": "cursor-implement",
            "source_ref": source_ref,
            "subject": _GOOD_SUBJECT,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["thread_id"] == "bus-packet-rt"
    assert body["source_ref"] == source_ref
    assert body["implement_spec_hash"].startswith("sha256:")
    assert body["materialization_mode"] == "auto"
    materialized = (
        tmp_path / "universal-llm-gateway/tmp/implement-admission/materialized"
    )
    assert not materialized.exists() or not any(materialized.iterdir())

    enriched_text = (tmp_path / _PACKET_RT_REL).read_text(encoding="utf-8")
    enriched_bytes = enriched_text.encode("utf-8")
    invariants = _extract_block(enriched_text, "invariants") or ""
    for slug in _DEFAULT_DENSIFY_SLUGS:
        ref_lines = [
            line
            for line in invariants.splitlines()
            if _line_references_slug(line, slug)
        ]
        assert len(ref_lines) == 1, f"{slug}: expected 1 ref line, got {ref_lines!r}"
    slug_lines = [
        line
        for line in invariants.splitlines()
        if ("Use the `" in line and " skill" in line)
        or ("Load skill:" in line and "`" in line)
    ]
    slugs_in_lines = re.findall(r"`([a-z0-9][-a-z0-9_]*)`", "\n".join(slug_lines))
    assert all(count == 1 for count in Counter(slugs_in_lines).values()), (
        f"duplicate canonical slug lines: {Counter(slugs_in_lines)!r}"
    )

    second = enrich_handoff_packet(enriched_text, cortex=_Phase2StubCortex())
    assert second.text.encode("utf-8") == enriched_bytes
    assert not second.changed


def test_s4_packet_lane_skips_gate_b_both_present(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Gate B removed for packet lane when source_ref and packet_path present."""
    from .implement_admission_bridge import verify_both_present_hash

    monkeypatch.setenv("PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("ALLOW_UNSET_AGENT_BUS_TOKEN", "true")
    rel = _PACKET_RT_REL
    source_ref = f"packet:{rel}"
    _write_packet(
        tmp_path,
        rel,
        _packet_with_source_ref_frontmatter(source_ref, _CONFORMANT_PACKET),
    )
    _patch_enforce_all_gates(monkeypatch)

    spec_hash = verify_both_present_hash(
        request_id="req-gb-skip",
        source_ref=source_ref,
        packet_path=rel,
        cortex=_Phase2StubCortex(),
        workspaces_root=tmp_path,
    ).implement_spec_hash
    assert spec_hash.startswith("sha256:")

    _patch_phase2_reader(monkeypatch)
    _patch_bus(monkeypatch, _make_bus_transport(thread_id="bus-packet-gb"))
    client = TestClient(
        _route_app(monkeypatch, tmp_path), raise_server_exceptions=False
    )
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


def test_s4_packet_path_only_decision_gate_enforced(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """require_decision_asserted runs on packet_path-only lane (S3)."""
    monkeypatch.setenv("PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("ALLOW_UNSET_AGENT_BUS_TOKEN", "true")
    _write_packet(
        tmp_path,
        _PACKET_RT_REL,
        _packet_with_source_ref_frontmatter(
            f"packet:{_PACKET_RT_REL}", _CONFORMANT_PACKET
        ),
    )
    client = TestClient(
        _route_app(monkeypatch, tmp_path), raise_server_exceptions=False
    )
    _patch_phase2_reader(monkeypatch, _Phase2BelievedOnlyCortex())
    resp = client.post(
        "/api/v1/team/handoff",
        json={
            "op": "handoff",
            "role": "cursor-implement",
            "packet_path": _PACKET_RT_REL,
            "subject": _GOOD_SUBJECT,
        },
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "decision_not_asserted"


def test_s4_resolve_packet_ref_no_materialize(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """resolve_source_ref_to_packet returns authored path without materializing."""
    from .implement_admission_bridge import resolve_source_ref_to_packet

    monkeypatch.setenv("PROJECT_ROOT", str(tmp_path))
    _write_packet(tmp_path, _PACKET_RT_REL, _CONFORMANT_PACKET)
    source_ref = f"packet:{_PACKET_RT_REL}"
    result = resolve_source_ref_to_packet(
        source_ref,
        cortex=_Phase2StubCortex(),
        workspaces_root=tmp_path,
        request_id="req-no-mat",
    )
    assert not result.gated
    assert result.packet_path == _PACKET_RT_REL
    assert result.packet_sha256 is not None
    materialized = (
        tmp_path / "universal-llm-gateway/tmp/implement-admission/materialized"
    )
    assert not materialized.exists() or not any(materialized.iterdir())


# ---------------------------------------------------------------------------
# Dispatch defaults bundle — executor + consult-review advisories (thread 1530)
# ---------------------------------------------------------------------------

_DD_REL = "universal-llm-gateway/tmp/reviews/dd-bundle-packet.md"


def test_dd_implement_default_composer_fields(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALLOW_UNSET_AGENT_BUS_TOKEN", "true")
    _write_packet(tmp_path, _DD_REL, _CONFORMANT_PACKET)
    _patch_bus(monkeypatch, _make_bus_transport(thread_id="bus-dd-default"))

    client = TestClient(
        _route_app(monkeypatch, tmp_path), raise_server_exceptions=False
    )
    resp = client.post(
        "/api/v1/team/handoff",
        json={
            "op": "handoff",
            "role": "cursor-implement",
            "packet_path": _DD_REL,
            "subject": _GOOD_SUBJECT,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["recommended_executor"] == "composer"
    assert body["recommended_executor_source"] == "server_default:contract_implement"
    assert body["executor_bindable"] is True
    assert body["recommended_review"] is None
    assert "opus" not in body["push_reminder"].lower()
    assert "composer" in body["push_reminder"].lower()


def test_dd_consult_review_default_on(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALLOW_UNSET_AGENT_BUS_TOKEN", "true")
    _write_packet(tmp_path, _DD_REL, _CONSULT_ONLY_PACKET)
    _patch_bus(monkeypatch, _make_bus_transport(thread_id="bus-dd-consult"))

    client = TestClient(
        _route_app(monkeypatch, tmp_path), raise_server_exceptions=False
    )
    resp = client.post(
        "/api/v1/team/handoff",
        json={
            "op": "handoff",
            "role": "web-consult",
            "packet_path": _DD_REL,
            "subject": _GOOD_SUBJECT,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["recommended_review"] == "cross-family-reconcile:default-on"
    assert body.get("recommended_executor") is None


def test_dd_acceptance_gate_unchanged(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    packet = _CONFORMANT_PACKET.replace(
        "## Acceptance criteria\n1. It works.", "Just do the work."
    )
    _write_packet(tmp_path, _DD_REL, packet)
    client = TestClient(
        _route_app(monkeypatch, tmp_path), raise_server_exceptions=False
    )
    resp = client.post(
        "/api/v1/team/handoff",
        json={
            "op": "handoff",
            "role": "cursor-implement",
            "packet_path": _DD_REL,
            "subject": _GOOD_SUBJECT,
        },
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "handoff_packet_missing_acceptance"


# ---------------------------------------------------------------------------
# Phase 3 — cursor-sdk automated seat routing (thread 1561)
# ---------------------------------------------------------------------------


def test_t1_cursor_sdk_profile_load() -> None:
    from agent_seat.profiles import get_profile

    profile = get_profile("cursor", "sdk")
    assert profile.provider == "cursor"
    assert profile.tool_surface == "sdk"
    assert profile.auto_dispatchable is True
    assert profile.api_dispatchable is False
    assert profile.manual_handoff is False


def test_t1_resolve_cursor_sdk_handoff_seat() -> None:
    to_agent, family, platform, resolved_model = resolve_cursor_sdk_handoff_seat(
        "cursor-sdk",
        request_id="req-t1",
    )
    assert to_agent == "cursor-sdk"
    assert family == "cursor"
    assert platform == "sdk"
    assert resolved_model == "cursor/composer-2.5"


def test_t3_cursor_sdk_handoff_rejects_seat_not_manual(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALLOW_UNSET_AGENT_BUS_TOKEN", "true")
    _write_packet(tmp_path, _GOOD_PACKET, _CONFORMANT_PACKET)
    _patch_bus(monkeypatch, _make_bus_transport(thread_id="bus-cursor-sdk"))

    client = TestClient(
        _route_app(monkeypatch, tmp_path), raise_server_exceptions=False
    )
    resp = client.post(
        "/api/v1/team/handoff",
        json={
            "op": "handoff",
            "seat": "cursor-sdk",
            "packet_path": _GOOD_PACKET,
            "contract": "implement",
            "subject": _GOOD_SUBJECT,
        },
    )
    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert body["error"]["code"] == "seat_not_manual"


def test_t3b_cursor_sdk_generate_admits_and_dispatches_worker(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALLOW_UNSET_AGENT_BUS_TOKEN", "true")
    _write_packet(tmp_path, _GOOD_PACKET, _CONFORMANT_PACKET)
    _patch_bus(monkeypatch, _make_bus_transport(thread_id="bus-cursor-sdk-gen"))

    worker_calls: list[dict[str, Any]] = []

    async def _fake_dispatch(**kwargs: Any) -> tuple[bool, dict[str, Any]]:
        worker_calls.append(kwargs)
        return _WORKER_ADMIT_OK

    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_generate.dispatch_cursor_sdk_worker",
        _fake_dispatch,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_generate.derive_cursor_sdk_prompt_preamble",
        lambda **_kwargs: "",
    )

    client = TestClient(
        _route_app(monkeypatch, tmp_path), raise_server_exceptions=False
    )
    resp = client.post(
        "/api/v1/team/dispatch",
        json={
            "op": "generate",
            "seat": "cursor-sdk",
            "dispatch_thread_id": "dispatch-thread-sdk-gen",
            "contract": "implement",
            "packet_path": _GOOD_PACKET,
        },
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["substrate"] == "sdk"
    assert body["output_contract"] == "thread"
    assert body["thread_id"] == "bus-cursor-sdk-gen"
    assert body["execution_id"]
    assert body["poll_hint"]["tool"] == "wait"
    assert body["poll_hint"]["arguments"]["from_agent"] == "cursor-sdk"
    assert body["to_agent"].startswith("cursor-sdk:dispatch:")
    assert len(worker_calls) == 1
    assert worker_calls[0]["thread_id"] == "bus-cursor-sdk-gen"


def test_t3c_cursor_sdk_generate_consult_uses_message(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALLOW_UNSET_AGENT_BUS_TOKEN", "true")
    _patch_bus(monkeypatch, _make_bus_transport(thread_id="bus-sdk-msg"))

    msg_calls: list[dict[str, Any]] = []

    async def _fake_msg(**kwargs: Any) -> tuple[bool, dict[str, Any]]:
        msg_calls.append(kwargs)
        return _WORKER_ADMIT_OK

    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_generate.dispatch_cursor_sdk_worker_message",
        _fake_msg,
    )

    async def _fake_thread_read(**_kwargs: Any) -> str:
        # Folded wire: consult context comes from the dispatch thread, not messages[].
        return "Review this design."

    monkeypatch.setattr(
        "systems.frontier_consult.generate_wrap.read_latest_dispatch_thread_body",
        _fake_thread_read,
    )

    client = TestClient(
        _route_app(monkeypatch, tmp_path), raise_server_exceptions=False
    )
    resp = client.post(
        "/api/v1/team/dispatch",
        json={
            "op": "generate",
            "seat": "cursor-sdk",
            "dispatch_thread_id": "dt-consult",
            "contract": "light-bounded",
        },
    )
    assert resp.status_code == 202, resp.text
    assert msg_calls[0]["message"] == "Review this design."


def test_t6a_cursor_sdk_seat_capability(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALLOW_UNSET_AGENT_BUS_TOKEN", "true")
    _write_packet(tmp_path, _GOOD_PACKET, _CONFORMANT_PACKET)
    _patch_bus(monkeypatch, _make_bus_transport(thread_id="bus-t6a"))

    async def _fake_dispatch(**kwargs: Any) -> tuple[bool, dict[str, Any]]:
        return _WORKER_ADMIT_OK

    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_generate.dispatch_cursor_sdk_worker",
        _fake_dispatch,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_generate.derive_cursor_sdk_prompt_preamble",
        lambda **_kwargs: "",
    )

    client = TestClient(
        _route_app(monkeypatch, tmp_path), raise_server_exceptions=False
    )
    resp = client.post(
        "/api/v1/team/dispatch",
        json={
            "op": "generate",
            "seat": "cursor-sdk",
            "dispatch_thread_id": "dt-t6a",
            "contract": "implement",
            "packet_path": _GOOD_PACKET,
        },
    )
    assert resp.status_code == 202, resp.text
    sc = resp.json()["capabilities"]
    assert sc["role"] == "cursor-sdk"
    assert sc["substrate"] == "sdk"
    assert sc["tool_surface"] == "sdk"
    assert sc["resolved_model"] == "cursor/composer-2.5"
    assert sc["inline_only"] is False
    assert sc["tool_access"] is True
    assert sc["mcp_mechanism"] == "local_native"
    assert "mcp_connector_active" not in sc


def test_t6b_web_consult_seat_capability_matches_profile(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from agent_seat.profiles import get_profile

    from .admission import resolve_handoff_target

    monkeypatch.setenv("ALLOW_UNSET_AGENT_BUS_TOKEN", "true")
    _write_packet(tmp_path, _DD_REL, _CONSULT_ONLY_PACKET)
    _patch_bus(monkeypatch, _make_bus_transport(thread_id="bus-t6b"))

    client = TestClient(
        _route_app(monkeypatch, tmp_path), raise_server_exceptions=False
    )
    resp = client.post(
        "/api/v1/team/handoff",
        json={
            "op": "handoff",
            "role": "web-consult",
            "packet_path": _DD_REL,
            "subject": _GOOD_SUBJECT,
        },
    )
    assert resp.status_code == 200, resp.text
    _to, fam, plat, _m = resolve_handoff_target(role="web-consult", request_id="t6b")
    expected = get_profile(fam, plat)
    sc = resp.json()["seat_capability"]
    assert sc["delivery"] == expected.delivery == "manual"
    assert sc["api_dispatchable"] == expected.api_dispatchable is False
    assert sc["auto_dispatchable"] == expected.auto_dispatchable is False
    assert sc["manual_handoff"] == expected.manual_handoff is True
    assert sc["tool_surface"] == expected.tool_surface
    assert sc["picker_range"] == list(expected.allowed_models)
    assert sc["default_model"] == expected.default_model
    assert sc["recommended_executor"] is None


def test_t4_cursor_sdk_role_rejected_on_generate() -> None:
    with pytest.raises(FrontierEndpointError) as exc_info:
        enforce_team_dispatch_generate_admit("cursor-sdk", request_id="req-t4")
    assert exc_info.value.code == "role_is_not_a_seat"


def test_t4b_resolve_auto_seat_generate_target_default_model() -> None:
    from .admission import resolve_auto_seat_generate_target

    to_agent, family, platform, model = resolve_auto_seat_generate_target(
        "cursor-sdk", model=None, request_id="req-t4b"
    )
    assert to_agent == "cursor-sdk"
    assert family == "cursor"
    assert platform == "sdk"
    assert model == "cursor/composer-2.5"


def test_t4c_resolve_auto_seat_generate_target_explicit_model() -> None:
    from .admission import resolve_auto_seat_generate_target

    _to, _f, _p, model = resolve_auto_seat_generate_target(
        "cursor-sdk",
        model="cursor/claude-opus-4-8",
        request_id="req-t4c",
    )
    assert model == "cursor/claude-opus-4-8"


def test_t4d_reviewer_still_api_dispatchable() -> None:
    enforce_team_dispatch_generate_admit("reviewer", request_id="req-t4d")


def test_t4e_claude_web_still_rejected_on_generate() -> None:
    with pytest.raises(FrontierEndpointError) as exc_info:
        enforce_team_dispatch_generate_admit("claude-web", request_id="req-t4e")
    assert exc_info.value.code == "role_not_api_dispatchable"


def test_t5_cursor_sdk_handoff_seat_not_manual(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Handoff seat=cursor-sdk surfaces seat_not_manual (alias removed)."""
    monkeypatch.setenv("ALLOW_UNSET_AGENT_BUS_TOKEN", "true")
    _write_packet(tmp_path, _GOOD_PACKET, _CONFORMANT_PACKET)
    _patch_bus(monkeypatch, _make_bus_transport(thread_id="bus-cursor-sdk-down"))

    client = TestClient(
        _route_app(monkeypatch, tmp_path), raise_server_exceptions=False
    )
    resp = client.post(
        "/api/v1/team/handoff",
        json={
            "op": "handoff",
            "seat": "cursor-sdk",
            "packet_path": _GOOD_PACKET,
            "contract": "implement",
            "subject": _GOOD_SUBJECT,
        },
    )
    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert body["error"]["code"] == "seat_not_manual"


def test_t7_handoff_cursor_sdk_rejects_seat_not_manual(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALLOW_UNSET_AGENT_BUS_TOKEN", "true")
    _write_packet(tmp_path, _GOOD_PACKET, _CONFORMANT_PACKET)
    _patch_bus(monkeypatch, _make_bus_transport(thread_id="bus-alias"))

    client = TestClient(
        _route_app(monkeypatch, tmp_path), raise_server_exceptions=False
    )
    resp = client.post(
        "/api/v1/team/handoff",
        json={
            "op": "handoff",
            "seat": "cursor-sdk",
            "packet_path": _GOOD_PACKET,
            "contract": "implement",
            "subject": _GOOD_SUBJECT,
        },
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"]["code"] == "seat_not_manual"


# ---------------------------------------------------------------------------
# Diff-prohibition — packet_contains_diff_text (A1/A2)
# ---------------------------------------------------------------------------

_DIFF_REL = "universal-llm-gateway/tmp/reviews/diff-guard-packet.md"

_REAL_UNIFIED_DIFF = """\
diff --git a/foo.py b/foo.py
index 1111111..2222222 100644
--- a/foo.py
+++ b/foo.py
@@ -1,3 +1,4 @@
 def foo():
+    return 1
     pass
"""


def test_diff_guard_yaml_frontmatter_admits(tmp_path: Path) -> None:
    packet = f"---\nsource_ref: todo:diff-guard\n---\n{_CONFORMANT_PACKET}"
    _write_packet(tmp_path, _DIFF_REL, packet)
    validate_packet(
        request_id="req-diff-yaml",
        packet_path=_DIFF_REL,
        to_agent="claude-web",
        handoff_contract="consult",
        workspaces_root=tmp_path,
    )


def test_diff_guard_isolated_hunk_mention_admits(tmp_path: Path) -> None:
    packet = _CONFORMANT_PACKET.replace(
        "<corpus>the artifact</corpus>",
        "<corpus>grep '^@@' for hunk headers in logs.</corpus>",
    )
    _write_packet(tmp_path, _DIFF_REL, packet)
    validate_packet(
        request_id="req-diff-mention",
        packet_path=_DIFF_REL,
        to_agent="claude-web",
        handoff_contract="consult",
        workspaces_root=tmp_path,
    )


def test_diff_guard_prose_dashes_admit(tmp_path: Path) -> None:
    packet = _CONFORMANT_PACKET.replace(
        "<corpus>the artifact</corpus>",
        "<corpus>Email quoted line:\n> ---\nProse separator --- still fine.</corpus>",
    )
    _write_packet(tmp_path, _DIFF_REL, packet)
    validate_packet(
        request_id="req-diff-prose",
        packet_path=_DIFF_REL,
        to_agent="claude-web",
        handoff_contract="consult",
        workspaces_root=tmp_path,
    )


def test_diff_guard_real_unified_diff_rejected(tmp_path: Path) -> None:
    packet = _CONFORMANT_PACKET.replace(
        "<corpus>the artifact</corpus>",
        f"<corpus>\n{_REAL_UNIFIED_DIFF}\n</corpus>",
    )
    _write_packet(tmp_path, _DIFF_REL, packet)
    with pytest.raises(FrontierEndpointError) as exc_info:
        validate_packet(
            request_id="req-diff-reject",
            packet_path=_DIFF_REL,
            to_agent="claude-web",
            handoff_contract="consult",
            workspaces_root=tmp_path,
        )
    assert exc_info.value.code == "handoff_packet_contains_diff_text"


def test_diff_guard_detector_matrix() -> None:
    from .diff_text_guard import packet_contains_diff_text

    assert not packet_contains_diff_text("---\nsource_ref: todo:x\n---\nbody")
    assert not packet_contains_diff_text("mention @@ in prose only")
    assert not packet_contains_diff_text("> ---\nProse --- separator")
    assert packet_contains_diff_text(_REAL_UNIFIED_DIFF)
