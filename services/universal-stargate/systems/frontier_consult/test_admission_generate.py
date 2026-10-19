"""Admission tests for team_dispatch generate/to_thread guards."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from agent_seat import AgentMeta, HydrationBundle
from agent_seat.dispatch_role_catalog import auto_seats, generate_roles
from fastapi import Response
from fastapi.responses import JSONResponse
from implement_admission.routing import load_route_policy
from implement_admission.workflow_registry import load_auto_judgment_default_model
from pipelines.team_dispatch_admit.v1.handlers import test_handlers as admit_pipeline

from . import cursor_sdk_generate_prepare as prepare_mod
from .admission import (
    FrontierEndpointError,
    enforce_team_dispatch_generate_admit,
    is_chat_completions_only,
    reject_role_cursor_sdk_on_generate,
)
from .route import TeamDispatchGenerateBody, TeamDispatchToThreadBody, team_dispatch
from .service import FrontierGenerateRequest, build_dispatch_body
from .team_dispatch_admit_client import (
    run_team_dispatch_admit as _real_run_team_dispatch_admit,
)

enforce_generate_role_seat_exclusive = (
    admit_pipeline.enforce_generate_role_seat_exclusive
)
is_auto_seat_generate_admission = admit_pipeline.is_auto_seat_generate_admission
resolve_auto_seat_generate_target = admit_pipeline.resolve_auto_seat_generate_target

_DISPATCH_THREAD = "test-dispatch-thread"


def _mock_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    proxy = MagicMock()
    proxy.event_bus = None
    monkeypatch.setattr("systems.proxy.dependencies.get_proxy", lambda: proxy)


def _mock_proxy_recording_publish(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[list[object], list[asyncio.AbstractEventLoop]]:
    events: list[object] = []
    publish_loops: list[asyncio.AbstractEventLoop] = []
    proxy = MagicMock()

    def _publish_from_sync(event: object) -> None:
        publish_loops.append(asyncio.get_running_loop())
        events.append(event)

    proxy.event_bus = MagicMock()
    proxy.event_bus.publish_from_sync = _publish_from_sync
    monkeypatch.setattr("systems.proxy.dependencies.get_proxy", lambda: proxy)
    return events, publish_loops


def _restore_real_team_dispatch_admit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "systems.frontier_consult.team_dispatch_admit_client.run_team_dispatch_admit",
        _real_run_team_dispatch_admit,
    )


def _admit_client_mock(*, status_code: int, plan: dict[str, object]) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    if status_code < 400:
        resp.json.return_value = {
            "choices": [{"message": {"content": json.dumps(plan)}}],
        }
        resp.raise_for_status = MagicMock()
    client = MagicMock()
    client.post.return_value = resp
    client.__enter__ = MagicMock(return_value=client)
    client.__exit__ = MagicMock(return_value=False)
    return client


def test_g1_claude_web_seat_rejected() -> None:
    with pytest.raises(FrontierEndpointError) as exc_info:
        enforce_team_dispatch_generate_admit("claude-web", request_id="req-g1")
    err = exc_info.value
    assert err.status_code == 422
    assert err.code == "role_not_api_dispatchable"
    assert err.field == "role"
    assert "claude-web" in err.reason


def test_g2_web_consult_role_rejected_on_generate() -> None:
    with pytest.raises(FrontierEndpointError) as exc_info:
        enforce_team_dispatch_generate_admit("web-consult", request_id="req-g2")
    err = exc_info.value
    assert err.code == "role_not_api_dispatchable"
    assert "web-consult" in err.reason


def test_g3_reviewer_role_admitted() -> None:
    enforce_team_dispatch_generate_admit("reviewer", request_id="req-g3")


def test_g4_role_cursor_sdk_rejected_on_generate() -> None:
    with pytest.raises(FrontierEndpointError) as exc_info:
        reject_role_cursor_sdk_on_generate("cursor-sdk", request_id="req-g4")
    err = exc_info.value
    assert err.code == "role_is_not_a_seat"
    assert err.field == "role"
    assert 'seat="cursor-sdk"' in err.reason


def test_g4b_seat_cursor_sdk_admitted_on_generate() -> None:
    resolve_auto_seat_generate_target("cursor-sdk", model=None, request_id="req-g4b")


def test_conductor_omit_model_resolves_profile_default() -> None:
    _, _, _, resolved = resolve_auto_seat_generate_target(
        "cursor-sdk",
        model=None,
        request_id="req-conductor-omit",
    )
    assert resolved == "cursor/grok-4.7"
    assert resolved == load_auto_judgment_default_model(load_route_policy())


def test_conductor_body_kind_omit_model_resolves_judgment_default() -> None:
    """Conductor in packet body (no wire packet_kind) + omit model → auto_judgment."""
    from implement_admission.dispatch_topic import extract_packet_kind_from_body

    body = "---\npacket_kind: conductor\ncontract: conductor\n---\nscope"
    wire_kind = None
    effective_kind = (wire_kind or "").strip().lower() or None
    if body and not effective_kind:
        effective_kind = extract_packet_kind_from_body(body)
    assert effective_kind == "conductor"
    assert extract_packet_kind_from_body(body) == "conductor"
    _, _, _, resolved = resolve_auto_seat_generate_target(
        "cursor-sdk",
        model=None,
        request_id="req-body-conductor",
    )
    assert resolved == "cursor/grok-4.7"


def test_conductor_explicit_grok_pin_honored() -> None:
    _, _, _, resolved = resolve_auto_seat_generate_target(
        "cursor-sdk",
        model="cursor/grok-4.7",
        request_id="req-conductor-grok-pin",
    )
    assert resolved == "cursor/grok-4.7"


def test_omit_model_without_packet_kind_resolves_judgment_default() -> None:
    _, _, _, resolved = resolve_auto_seat_generate_target(
        "cursor-sdk",
        model=None,
        request_id="req-omit-none",
    )
    assert resolved == "cursor/grok-4.7"


def test_omit_model_by_job() -> None:
    _, _, _, implement = resolve_auto_seat_generate_target(
        "cursor-sdk", model=None, request_id="req-impl", job="implement"
    )
    _, _, _, mechanical = resolve_auto_seat_generate_target(
        "cursor-sdk", model=None, request_id="req-mech", job="mechanical"
    )
    _, _, _, conductor = resolve_auto_seat_generate_target(
        "cursor-sdk", model=None, request_id="req-cond", job="conductor"
    )
    _, _, _, pinned = resolve_auto_seat_generate_target(
        "cursor-sdk",
        model="cursor/grok-4.7",
        request_id="req-pin",
        job="implement",
    )
    assert implement == "cursor/composer-2.5"
    assert mechanical == "cursor/composer-2.5"
    assert conductor == "cursor/grok-4.7"
    assert pinned == "cursor/grok-4.7"


def test_explicit_composer_with_conductor_packet_kind() -> None:
    _, _, _, resolved = resolve_auto_seat_generate_target(
        "cursor-sdk",
        model="cursor/composer-2.5",
        request_id="req-explicit-composer",
    )
    assert resolved == "cursor/composer-2.5"


def test_role_seat_exclusive_rejected() -> None:
    with pytest.raises(FrontierEndpointError) as exc_info:
        enforce_generate_role_seat_exclusive(
            "reviewer", "cursor-sdk", request_id="req-excl"
        )
    assert exc_info.value.code == "role_seat_exclusive"


def test_role_or_seat_required_rejected() -> None:
    with pytest.raises(FrontierEndpointError) as exc_info:
        enforce_generate_role_seat_exclusive(None, None, request_id="req-req")
    assert exc_info.value.code == "role_or_seat_required"


def test_auto_seats_roster() -> None:
    assert auto_seats() == ["cursor-sdk"]


def test_generate_roles_api_only() -> None:
    assert "cursor-sdk" not in generate_roles()


def test_auto_seat_generate_admission_by_seat() -> None:
    assert is_auto_seat_generate_admission(
        seat="cursor-sdk", role=None, model=None, request_id="req-seat"
    )


def test_auto_seat_generate_admission_rejects_role_cursor_sdk() -> None:
    assert not is_auto_seat_generate_admission(
        seat=None, role="cursor-sdk", model=None, request_id="req-role"
    )


def test_route_policy_vocab_conformance() -> None:
    from implement_admission.routing import verify_role_substrate_vocab_conformance

    assert verify_role_substrate_vocab_conformance() == []


def test_cursor_consult_role_rejected_on_generate() -> None:
    """cursor-consult resolves to claude-cursor (manual_handoff) → 422."""
    with pytest.raises(FrontierEndpointError) as exc_info:
        enforce_team_dispatch_generate_admit("cursor-consult", request_id="req-cl")
    err = exc_info.value
    assert err.status_code == 422
    assert err.code == "role_not_api_dispatchable"
    assert err.field == "role"


def test_cursor_implement_role_rejected_on_generate() -> None:
    """cursor-implement resolves to claude-cursor (handoff-only) → 422 on generate."""
    with pytest.raises(FrontierEndpointError) as exc_info:
        enforce_team_dispatch_generate_admit("cursor-implement", request_id="req-impl")
    err = exc_info.value
    assert err.status_code == 422
    assert err.code == "role_not_api_dispatchable"
    assert err.field == "role"


def _bundle(meta: AgentMeta) -> HydrationBundle:
    return HydrationBundle(briefing_card_md="# briefing", agent_meta=meta)


@pytest.mark.asyncio
async def test_g4_build_dispatch_body_rejects_claude_web_even_with_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_hydrate(
        agent: str, transcript_id: str | None = None, **_k: Any
    ) -> HydrationBundle:
        return _bundle(
            AgentMeta(
                default_model=None,
                allowed_models=[],
                allowed_options=None,
            ),
        )

    monkeypatch.setattr(
        "systems.frontier_consult.service.hydrate_agent",
        fake_hydrate,
    )

    req = FrontierGenerateRequest(
        messages=[{"role": "user", "content": "hello"}],
        role="claude-web",
        dispatch_thread_id=_DISPATCH_THREAD,
        model="anthropic/claude-opus-4-8",
    )
    with pytest.raises(FrontierEndpointError) as exc_info:
        await build_dispatch_body(req)
    assert exc_info.value.code in {
        "web_seat_not_generate_target",
        "role_not_api_dispatchable",
    }


def test_openrouter_model_is_chat_completions_only() -> None:
    assert is_chat_completions_only("openrouter/writer/palmyra-x5") is True


def test_native_openai_model_not_chat_completions_only() -> None:
    assert is_chat_completions_only("openai/gpt-5.5") is False


@pytest.mark.asyncio
async def test_route_gatherer_cursor_model_role_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_proxy(monkeypatch)
    create = AsyncMock()
    admit = AsyncMock()
    monkeypatch.setattr(prepare_mod, "create_handoff_thread", create)
    monkeypatch.setattr(prepare_mod, "admit_handoff_dispatch", admit)
    body = TeamDispatchGenerateBody(
        op="generate",
        role="gatherer",
        model="cursor/claude-opus-5",
        dispatch_thread_id="todo:arc",
        job="freeform",
    )
    result = await team_dispatch(body, Response())
    assert isinstance(result, JSONResponse)
    assert result.status_code == 422
    payload = result.body.decode()
    assert "substrate_model_role_conflict" in payload
    assert '"field":"role"' in payload.replace(" ", "")
    create.assert_not_awaited()
    admit.assert_not_awaited()


@pytest.mark.asyncio
async def test_route_claude_web_seat_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_proxy(monkeypatch)
    create = AsyncMock()
    admit = AsyncMock()
    monkeypatch.setattr(prepare_mod, "create_handoff_thread", create)
    monkeypatch.setattr(prepare_mod, "admit_handoff_dispatch", admit)
    body = TeamDispatchGenerateBody(
        op="generate",
        seat="claude-web",
        dispatch_thread_id="todo:arc",
        job="freeform",
    )
    result = await team_dispatch(body, Response())
    assert isinstance(result, JSONResponse)
    assert result.status_code == 422
    assert "seat_unknown" in result.body.decode()
    create.assert_not_awaited()
    admit.assert_not_awaited()


@pytest.mark.asyncio
async def test_route_cdp_model_only_admits_without_role_seat_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_proxy(monkeypatch)
    cdp = AsyncMock(return_value={"execution_id": "e1", "thread_id": "t1"})
    monkeypatch.setattr(
        "systems.frontier_consult.route.dispatch_cdp_generate",
        cdp,
    )
    body = TeamDispatchGenerateBody(
        op="generate",
        model="cdp/opus-5",
        dispatch_thread_id="todo:arc",
        job="freeform",
        packet_path="tmp/reviews/packet.md",
        caller_agent="test-lead",
        work_key="todo:arc",
    )
    result = await team_dispatch(body, Response())
    assert result == {"execution_id": "e1", "thread_id": "t1"}
    cdp.assert_awaited_once()


@pytest.mark.asyncio
async def test_route_to_thread_omit_role_frontier_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_proxy(monkeypatch)
    dispatch = AsyncMock(return_value={"execution_id": "exec-frontier"})
    monkeypatch.setattr("systems.frontier_consult.route._dispatch", dispatch)
    body = TeamDispatchToThreadBody.model_construct(
        op="to_thread",
        thread="agent-bus:1",
        dispatch_thread_id="todo:arc",
        role="",
        prompt="hello",
        job="freeform",
    )
    await team_dispatch(body, Response())
    dispatch.assert_awaited_once()


@pytest.mark.asyncio
async def test_route_admit_unavailable_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from systems.frontier_consult import team_dispatch_admit_client as admit_client

    _mock_proxy(monkeypatch)
    create = AsyncMock()
    admit = AsyncMock()
    monkeypatch.setattr(prepare_mod, "create_handoff_thread", create)
    monkeypatch.setattr(prepare_mod, "admit_handoff_dispatch", admit)

    resp = MagicMock()
    resp.status_code = 502
    client = MagicMock()
    client.post.return_value = resp
    client.__enter__ = MagicMock(return_value=client)
    client.__exit__ = MagicMock(return_value=False)

    async def _sync_admit(**kwargs: object) -> dict[str, object]:
        import asyncio

        body = kwargs["body"]
        request_id = str(kwargs["request_id"])
        publisher = kwargs.get("event_publisher")
        return await asyncio.to_thread(
            admit_client.run_team_dispatch_admit_sync,
            body=body,
            request_id=request_id,
            event_publisher=publisher,
        )

    monkeypatch.setattr(
        "systems.frontier_consult.team_dispatch_admit_client.run_team_dispatch_admit",
        _sync_admit,
    )
    with patch(
        "systems.frontier_consult.team_dispatch_admit_client.make_sync_client",
        return_value=client,
    ):
        body = TeamDispatchGenerateBody(
            op="generate",
            seat="cursor-sdk",
            model="cursor/grok-4.7",
            dispatch_thread_id="todo:arc",
            job="freeform",
            lane="B",
            packet_path="tmp/reviews/packet.md",
        )
        result = await team_dispatch(body, Response())
    assert isinstance(result, JSONResponse)
    assert result.status_code == 503
    assert "team_dispatch_admit_unavailable" in result.body.decode()
    create.assert_not_awaited()
    admit.assert_not_awaited()


@pytest.mark.asyncio
async def test_route_admit_event_published_on_loop_thread_503(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _restore_real_team_dispatch_admit(monkeypatch)
    events, publish_loops = _mock_proxy_recording_publish(monkeypatch)
    route_loop = asyncio.get_running_loop()
    client = _admit_client_mock(status_code=502, plan={})

    with patch(
        "systems.frontier_consult.team_dispatch_admit_client.make_sync_client",
        return_value=client,
    ):
        body = TeamDispatchGenerateBody(
            op="generate",
            seat="cursor-sdk",
            model="cursor/grok-4.7",
            dispatch_thread_id="todo:arc",
            job="freeform",
            lane="B",
            packet_path="tmp/reviews/packet.md",
            caller_agent="test-lead",
            work_key="todo:arc",
        )
        result = await team_dispatch(body, Response())

    assert isinstance(result, JSONResponse)
    assert result.status_code == 503
    assert "team_dispatch_admit_unavailable" in result.body.decode()
    assert len(events) == 1
    assert events[0].signal == "frontier.admit.unavailable"
    assert publish_loops[0] is route_loop


@pytest.mark.asyncio
async def test_route_admit_event_published_on_loop_thread_422(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _restore_real_team_dispatch_admit(monkeypatch)
    events, publish_loops = _mock_proxy_recording_publish(monkeypatch)
    route_loop = asyncio.get_running_loop()
    plan = {
        "admitted": False,
        "code": "role_or_seat_required",
        "field": "role",
        "status_code": 422,
        "reason": "missing",
    }
    client = _admit_client_mock(status_code=200, plan=plan)

    with patch(
        "systems.frontier_consult.team_dispatch_admit_client.make_sync_client",
        return_value=client,
    ):
        body = TeamDispatchGenerateBody(
            op="generate",
            model="cursor/grok-4.7",
            dispatch_thread_id="todo:arc",
            job="freeform",
            lane="B",
            packet_path="tmp/reviews/packet.md",
            caller_agent="test-lead",
            work_key="todo:arc",
        )
        result = await team_dispatch(body, Response())

    assert isinstance(result, JSONResponse)
    assert result.status_code == 422
    assert "role_or_seat_required" in result.body.decode()
    assert len(events) == 1
    assert events[0].signal == "frontier.admit.rejected"
    assert publish_loops[0] is route_loop


@pytest.mark.asyncio
async def test_route_admit_event_published_on_loop_thread_202(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _restore_real_team_dispatch_admit(monkeypatch)
    events, publish_loops = _mock_proxy_recording_publish(monkeypatch)
    route_loop = asyncio.get_running_loop()
    plan = {
        "admitted": True,
        "backend": "cursor_sdk",
        "seat": "cursor-sdk",
        "model": "cursor/grok-4.7",
    }
    client = _admit_client_mock(status_code=200, plan=plan)

    async def _stub_sdk_route(*, response: Response, **_: object) -> dict[str, str]:
        response.status_code = 202
        return {"dispatch_id": "d-admit-202"}

    sdk_route = AsyncMock(side_effect=_stub_sdk_route)
    monkeypatch.setattr(
        "systems.frontier_consult.route.dispatch_cursor_sdk_generate_route",
        sdk_route,
    )

    with patch(
        "systems.frontier_consult.team_dispatch_admit_client.make_sync_client",
        return_value=client,
    ):
        body = TeamDispatchGenerateBody(
            op="generate",
            seat="cursor-sdk",
            model="cursor/grok-4.7",
            dispatch_thread_id="todo:arc",
            job="freeform",
            lane="B",
            packet_path="tmp/reviews/packet.md",
            caller_agent="test-lead",
            work_key="todo:arc",
        )
        response = Response()
        result = await team_dispatch(body, response)

    assert result == {"dispatch_id": "d-admit-202"}
    assert response.status_code == 202
    sdk_route.assert_awaited_once()
    assert len(events) == 1
    assert events[0].signal == "frontier.admit.decided"
    assert publish_loops[0] is route_loop
