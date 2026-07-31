"""Admission tests for team_dispatch generate/to_thread guards."""

from __future__ import annotations

from typing import Any

import pytest
from agent_seat import AgentMeta, HydrationBundle
from agent_seat.dispatch_role_catalog import auto_seats, generate_roles

from .admission import (
    FrontierEndpointError,
    enforce_generate_role_seat_exclusive,
    enforce_team_dispatch_generate_admit,
    is_auto_seat_generate_admission,
    is_chat_completions_only,
    reject_role_cursor_sdk_on_generate,
    resolve_auto_seat_generate_target,
)
from .service import FrontierGenerateRequest, build_dispatch_body

_DISPATCH_THREAD = "test-dispatch-thread"


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
    resolve_auto_seat_generate_target(
        "cursor-sdk", model=None, request_id="req-g4b"
    )


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
