"""Tests for frontier consult admission service."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from agent_seat import AgentMeta, HydrationBundle
from fastapi import Response
from skills_mount import ResolvedSkillBundle

from systems.pipeline.core.execution.async_tracker import PipelineExecutionTracker

from .api_role_generate import dispatch_api_role_generate
from .route import TeamDispatchGenerateBody
from .service import (
    FrontierEndpointError,
    FrontierGenerateRequest,
    _code_touching_generate,
    build_dispatch_body,
)

_DISPATCH_THREAD = "test-dispatch-thread"


def _bundle(meta: AgentMeta) -> HydrationBundle:
    return HydrationBundle(briefing_card_md="# briefing", agent_meta=meta)


@pytest.mark.asyncio
async def test_permissive_persona_accepts_anything(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_hydrate(
        agent: str, transcript_id: str | None = None, **_k: Any
    ) -> HydrationBundle:
        return _bundle(
            AgentMeta(
                default_model="openai/gpt-5.4-mini",
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
        role="gatherer",
        dispatch_thread_id=_DISPATCH_THREAD,
        generation_options={"max_tokens": 12, "temperature": 0.2},
    )
    body = await build_dispatch_body(req)
    options = body["pipeline_options"]
    assert body["model"] == "team-dispatch"
    assert body["dispatch_thread_id"] == _DISPATCH_THREAD
    assert options["role"] == "gatherer"
    assert options["model"] == "openai/gpt-5.4"
    assert options["model_entity_id"] == "model:gpt-5.4"
    assert "tools" not in options
    assert options["mcp"] is True
    assert options["_endpoint_request_id"]


@pytest.mark.asyncio
async def test_build_dispatch_body_sets_knob_resolution_preview(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_hydrate(
        agent: str, transcript_id: str | None = None, **_k: Any
    ) -> HydrationBundle:
        return _bundle(
            AgentMeta(
                default_model="openai/gpt-5.4",
                allowed_models=["openai/gpt-5.4", "openai/gpt-5.5"],
                allowed_options=None,
            ),
        )

    monkeypatch.setattr(
        "systems.frontier_consult.service.hydrate_agent",
        fake_hydrate,
    )

    req = FrontierGenerateRequest(
        messages=[{"role": "user", "content": "hello"}],
        role="gatherer",
        dispatch_thread_id=_DISPATCH_THREAD,
        reasoning_effort="high",
    )
    body = await build_dispatch_body(req)
    preview = body["pipeline_options"]["_knob_resolution_preview"]
    assert preview["provenance"] == "preview"
    assert preview["resolved_model"] == body["pipeline_options"]["model"]
    assert preview["status"] == "mapped"
    assert preview["reasoning_native"] == {"effort": "high"}


@pytest.mark.asyncio
async def test_build_dispatch_body_knob_resolution_preview_includes_max_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_hydrate(
        agent: str, transcript_id: str | None = None, **_k: Any
    ) -> HydrationBundle:
        return _bundle(
            AgentMeta(
                default_model="openai/gpt-5.4",
                allowed_models=["openai/gpt-5.4", "openai/gpt-5.5"],
                allowed_options=None,
            ),
        )

    monkeypatch.setattr(
        "systems.frontier_consult.service.hydrate_agent",
        fake_hydrate,
    )

    req = FrontierGenerateRequest(
        messages=[{"role": "user", "content": "hello"}],
        role="gatherer",
        dispatch_thread_id=_DISPATCH_THREAD,
        reasoning_effort="high",
        generation_options={"max_tokens": 4096},
    )
    body = await build_dispatch_body(req)
    max_output = body["pipeline_options"]["_knob_resolution_preview"]["max_output"]
    assert set(max_output) == {
        "requested",
        "resolved",
        "decision",
        "floor",
        "ceiling",
    }
    assert max_output["requested"] == 4096
    assert max_output["resolved"] == 16384


@pytest.mark.asyncio
async def test_explicit_model_override_can_fill_any_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_hydrate(
        agent: str, transcript_id: str | None = None, **_k: Any
    ) -> HydrationBundle:
        return _bundle(
            AgentMeta(
                default_model="xai/grok-4.5",
                allowed_models=["xai/grok-4.5"],
                allowed_options=None,
            ),
        )

    monkeypatch.setattr(
        "systems.frontier_consult.service.hydrate_agent",
        fake_hydrate,
    )

    req = FrontierGenerateRequest(
        messages=[{"role": "user", "content": "x"}],
        role="skeptic",
        dispatch_thread_id=_DISPATCH_THREAD,
        model="openai/gpt-5.4",
    )
    body = await build_dispatch_body(req)
    assert body["model"] == "team-dispatch"
    assert body["pipeline_options"]["model"] == "openai/gpt-5.4"
    assert body["pipeline_options"]["model_entity_id"] == "model:gpt-5.4"
    assert body["pipeline_options"]["mcp"] is True


# test_strict_persona_rejects_tools removed — tools field retired per
# todo:retire-tools-param-from-dispatch-mcp-surface. The Stargate request
# schema no longer accepts ``tools``. team_dispatch has no caller mcp knob
# either; tool surface is card-derived. See
# test_team_dispatch_xai_agent_enables_mcp +
# test_team_dispatch_non_xai_agent_enables_mcp.


@pytest.mark.asyncio
async def test_team_dispatch_xai_agent_enables_mcp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """xAI team roles (skeptic, artisan) on grok-4.5 get mcp=True (standard card)."""

    async def fake_hydrate(
        agent: str, transcript_id: str | None = None, **_k: Any
    ) -> HydrationBundle:
        return _bundle(
            AgentMeta(
                default_model="xai/grok-4.5",
                allowed_models=["xai/grok-4.5"],
                allowed_options=None,
            ),
        )

    monkeypatch.setattr(
        "systems.frontier_consult.service.hydrate_agent",
        fake_hydrate,
    )

    req = FrontierGenerateRequest(
        messages=[{"role": "user", "content": "x"}],
        role="skeptic",
        dispatch_thread_id=_DISPATCH_THREAD,
    )
    body = await build_dispatch_body(req)
    assert body["pipeline_options"]["mcp"] is True
    caps = body["pipeline_options"]["_capability_preview"]
    assert caps["role"] == "skeptic"
    assert caps["inline_only"] is False
    assert caps["mcp_connector_active"] is True
    assert caps["resolved_model"] == "xai/grok-4.5"


@pytest.mark.asyncio
async def test_team_dispatch_non_xai_agent_enables_mcp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-xAI team roles (gatherer, synthesizer, reviewer) get mcp=True."""

    async def fake_hydrate(
        agent: str, transcript_id: str | None = None, **_k: Any
    ) -> HydrationBundle:
        return _bundle(
            AgentMeta(
                default_model="openai/gpt-5.4-mini",
                allowed_models=[],
                allowed_options=None,
            ),
        )

    monkeypatch.setattr(
        "systems.frontier_consult.service.hydrate_agent",
        fake_hydrate,
    )

    req = FrontierGenerateRequest(
        messages=[{"role": "user", "content": "x"}],
        role="gatherer",
        dispatch_thread_id=_DISPATCH_THREAD,
    )
    body = await build_dispatch_body(req)
    assert body["pipeline_options"]["mcp"] is True


@pytest.mark.asyncio
async def test_strict_persona_rejects_generation_options_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_hydrate(
        agent: str, transcript_id: str | None = None, **_k: Any
    ) -> HydrationBundle:
        return _bundle(
            AgentMeta(
                default_model="openai/gpt-5.4-mini",
                allowed_models=["openai/gpt-5.4-mini"],
                allowed_options=["max_tokens"],
            ),
        )

    monkeypatch.setattr(
        "systems.frontier_consult.service.hydrate_agent",
        fake_hydrate,
    )

    req = FrontierGenerateRequest(
        messages=[{"role": "user", "content": "x"}],
        role="gatherer",
        dispatch_thread_id=_DISPATCH_THREAD,
        model="openai/gpt-5.4-mini",
        generation_options={"temperature": 0.3},
    )
    with pytest.raises(FrontierEndpointError) as exc:
        await build_dispatch_body(req)
    assert exc.value.field == "generation_options"


@pytest.mark.asyncio
async def test_default_model_used_when_omitted(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_hydrate(
        agent: str, transcript_id: str | None = None, **_k: Any
    ) -> HydrationBundle:
        return _bundle(
            AgentMeta(
                default_model="openai/gpt-5.4",
                allowed_models=["openai/gpt-5.4", "openai/gpt-5.4-mini"],
                allowed_options=None,
            ),
        )

    monkeypatch.setattr(
        "systems.frontier_consult.service.hydrate_agent",
        fake_hydrate,
    )

    req = FrontierGenerateRequest(
        messages=[{"role": "user", "content": "x"}],
        role="gatherer",
        dispatch_thread_id=_DISPATCH_THREAD,
    )
    body = await build_dispatch_body(req)
    assert body["model"] == "team-dispatch"
    assert body["pipeline_options"]["role"] == "gatherer"
    assert body["pipeline_options"]["model"] == "openai/gpt-5.4"
    assert body["pipeline_options"]["model_entity_id"] == "model:gpt-5.4"


@pytest.mark.asyncio
async def test_request_id_is_propagated_and_persona_free_mcp_defaults_false() -> None:
    """Persona-free dispatch defaults to mcp=False (one-shot reasoning)."""
    req = FrontierGenerateRequest(
        messages=[{"role": "user", "content": "x"}],
        model="openai/gpt-5.4-mini",
    )
    body = await build_dispatch_body(req)
    assert body["model"] == "chat-dispatch"
    options: dict[str, Any] = body["pipeline_options"]
    assert options["_endpoint_request_id"]
    assert "tools" not in options
    assert options["mcp"] is False
    assert "dispatch_thread_id" not in body


@pytest.mark.asyncio
async def test_persona_free_mcp_true_propagates() -> None:
    """Persona-free dispatch honors caller-supplied mcp=True."""
    req = FrontierGenerateRequest(
        messages=[{"role": "user", "content": "x"}],
        model="openai/gpt-5.4-mini",
        mcp=True,
    )
    body = await build_dispatch_body(req)
    assert body["pipeline_options"]["mcp"] is True


@pytest.mark.asyncio
async def test_build_dispatch_body_forwards_server_tools_when_supplied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_hydrate(
        agent: str, transcript_id: str | None = None, **_k: Any
    ) -> HydrationBundle:
        return _bundle(
            AgentMeta(
                default_model="openai/gpt-5.4",
                allowed_models=["openai/gpt-5.4"],
                allowed_options=None,
            ),
        )

    monkeypatch.setattr(
        "systems.frontier_consult.service.hydrate_agent",
        fake_hydrate,
    )

    req = FrontierGenerateRequest(
        messages=[{"role": "user", "content": "x"}],
        role="gatherer",
        dispatch_thread_id=_DISPATCH_THREAD,
        server_tools=False,
    )
    body = await build_dispatch_body(req)
    assert body["pipeline_options"]["server_tools"] is False


@pytest.mark.asyncio
async def test_build_dispatch_body_omits_server_tools_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_hydrate(
        agent: str, transcript_id: str | None = None, **_k: Any
    ) -> HydrationBundle:
        return _bundle(
            AgentMeta(
                default_model="openai/gpt-5.4",
                allowed_models=["openai/gpt-5.4"],
                allowed_options=None,
            ),
        )

    monkeypatch.setattr(
        "systems.frontier_consult.service.hydrate_agent",
        fake_hydrate,
    )

    req = FrontierGenerateRequest(
        messages=[{"role": "user", "content": "x"}],
        role="gatherer",
        dispatch_thread_id=_DISPATCH_THREAD,
    )
    body = await build_dispatch_body(req)
    assert "server_tools" not in body["pipeline_options"]


@pytest.mark.asyncio
async def test_build_dispatch_body_server_tools_noop_on_card_empty_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Anthropic cards carry no server-side built-ins; knob is accepted as no-op."""
    async def fake_hydrate(
        agent: str, transcript_id: str | None = None, **_k: Any
    ) -> HydrationBundle:
        return _bundle(
            AgentMeta(
                default_model="anthropic/claude-sonnet-4-6",
                allowed_models=["anthropic/claude-sonnet-4-6"],
                allowed_options=None,
            ),
        )

    monkeypatch.setattr(
        "systems.frontier_consult.service.hydrate_agent",
        fake_hydrate,
    )

    req = FrontierGenerateRequest(
        messages=[{"role": "user", "content": "x"}],
        role="reviewer",
        dispatch_thread_id=_DISPATCH_THREAD,
        model="anthropic/claude-sonnet-4-6",
        server_tools=False,
    )
    body = await build_dispatch_body(req)
    assert body["pipeline_options"]["server_tools"] is False


# NOTE — Gemini is MCP-capable: ``agents.yaml`` ``gemini/api`` declares
# ``tool_surface: mcp`` (candidate seat; MCP tool loop verified by smoke), so
# ``inline_only_for_model`` returns False and the caller ``mcp`` knob is honored,
# not clamped. The prior inline-only-clamp expectations were stale. Two-surface
# nuance — gemini's capability differs by model shape (recent vs older models /
# generateContent surface variants) — is FUTURE WORK: add per-model-shape
# capability resolution rather than a family-uniform profile flag.
@pytest.mark.parametrize(
    ("model", "caller_mcp", "expected"),
    [
        # Gemini honors the caller knob (mcp-capable); None = frontier omitted
        # default OFF (one-shot), which is a default, not an inline-only clamp.
        ("google/gemini-3.5-flash", True, True),
        ("google/gemini-3.5-flash", None, False),
        ("google/gemini-2.5-pro", True, True),
        ("xai/grok-4.5", True, True),
        ("xai/grok-4.5", None, False),
        ("openai/gpt-5.4-mini", True, True),
        ("openai/gpt-5.4-mini", None, False),
        ("openai/gpt-5.4-mini", False, False),
    ],
)
def test_mcp_enabled_for_frontier_dispatch_inline_only_clamp(
    model: str, caller_mcp: bool | None, expected: bool
) -> None:
    from .admission import mcp_enabled_for_frontier_dispatch

    assert mcp_enabled_for_frontier_dispatch(model, caller_mcp) is expected


@pytest.mark.asyncio
async def test_persona_free_gemini_mcp_true_honored() -> None:
    """Gemini MCP-capable: persona-free frontier honors caller mcp=True."""
    req = FrontierGenerateRequest(
        messages=[{"role": "user", "content": "x"}],
        model="google/gemini-3.5-flash",
        mcp=True,
    )
    body = await build_dispatch_body(req)
    assert body["model"] == "chat-dispatch"
    assert body["pipeline_options"]["mcp"] is True


@pytest.mark.asyncio
async def test_max_tool_turns_propagates_to_top_level_pipeline_options() -> None:
    req = FrontierGenerateRequest(
        messages=[{"role": "user", "content": "x"}],
        model="openai/gpt-5.4-mini",
        max_tool_turns=42,
    )
    body = await build_dispatch_body(req)
    options: dict[str, Any] = body["pipeline_options"]
    assert options["max_tool_turns"] == 42
    assert "max_tool_turns" not in options["generation_parameters"]


@pytest.mark.asyncio
async def test_max_tool_turns_omitted_role_defaults_to_150() -> None:
    """Team-dispatch API role consults default to 150 tool turns when omitted."""
    from agent_seat.tool_loop_budget import API_DEFAULT_MAX_TOOL_TURNS

    req = FrontierGenerateRequest(
        messages=[{"role": "user", "content": "x"}],
        role="reviewer",
        dispatch_thread_id="dispatch-1",
        model="openai/gpt-5.4-mini",
    )
    body = await build_dispatch_body(req)
    assert body["pipeline_options"]["max_tool_turns"] == API_DEFAULT_MAX_TOOL_TURNS
    assert API_DEFAULT_MAX_TOOL_TURNS == 150


@pytest.mark.asyncio
async def test_max_tool_turns_omitted_persona_free_defers_to_handler() -> None:
    """Persona-free frontier omits max_tool_turns; handler default is 150."""
    req = FrontierGenerateRequest(
        messages=[{"role": "user", "content": "x"}],
        model="openai/gpt-5.4-mini",
    )
    body = await build_dispatch_body(req)
    assert "max_tool_turns" not in body["pipeline_options"]


@pytest.mark.asyncio
async def test_timeout_seconds_propagates_as_top_level_dispatch_key() -> None:
    req = FrontierGenerateRequest(
        messages=[{"role": "user", "content": "x"}],
        model="openai/gpt-5.4-mini",
        timeout_seconds=14_400,
    )
    body = await build_dispatch_body(req)
    assert body["timeout_seconds"] == 14_400
    assert "timeout_seconds" not in body["pipeline_options"]


@pytest.mark.asyncio
async def test_timeout_seconds_omitted_does_not_set_key() -> None:
    req = FrontierGenerateRequest(
        messages=[{"role": "user", "content": "x"}],
        model="openai/gpt-5.4-mini",
    )
    body = await build_dispatch_body(req)
    assert "timeout_seconds" not in body


@pytest.mark.asyncio
async def test_admits_role_less_chat_completions_only_model_exact() -> None:
    """Role-less CC-only models admit onto chat-dispatch (respond_cc branch)."""
    req = FrontierGenerateRequest(
        messages=[{"role": "user", "content": "x"}],
        model="openai/gpt-5-search-api",
    )
    body = await build_dispatch_body(req)
    assert body["model"] == "chat-dispatch"
    assert body["pipeline_options"]["model"] == "openai/gpt-5-search-api"
    assert body["pipeline_options"]["mcp"] is False


@pytest.mark.asyncio
async def test_rejects_role_carrying_cc_only_model_suffix_variant() -> None:
    """Role-carrying *-search-api still 422; message must not cite llm_generate."""
    req = FrontierGenerateRequest(
        messages=[{"role": "user", "content": "x"}],
        role="reviewer",
        model="openai/gpt-6-search-api",
        dispatch_thread_id=_DISPATCH_THREAD,
    )
    with pytest.raises(FrontierEndpointError) as exc:
        await build_dispatch_body(req)
    assert exc.value.field == "model"
    assert "Chat Completions-only" in exc.value.reason
    assert "llm_generate" not in exc.value.reason


@pytest.mark.asyncio
async def test_thread_output_contract_fails_without_agent_bus_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AGENT_BUS_TOKEN", raising=False)
    monkeypatch.delenv("ALLOW_UNSET_AGENT_BUS_TOKEN", raising=False)

    req = FrontierGenerateRequest(
        messages=[{"role": "user", "content": "x"}],
        model="openai/gpt-5.4-mini",
        output_contract="thread",
        target_thread="42",
    )
    with pytest.raises(FrontierEndpointError) as exc:
        await build_dispatch_body(req)
    assert exc.value.status_code == 503
    assert "AGENT_BUS_TOKEN" in exc.value.reason


@pytest.mark.asyncio
async def test_team_dispatch_requires_dispatch_thread_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_hydrate(
        agent: str, transcript_id: str | None = None, **_k: Any
    ) -> HydrationBundle:
        return _bundle(AgentMeta(default_model="openai/gpt-5.4-mini"))

    monkeypatch.setattr(
        "systems.frontier_consult.service.hydrate_agent",
        fake_hydrate,
    )
    req = FrontierGenerateRequest(
        messages=[{"role": "user", "content": "x"}],
        role="reviewer",
    )
    with pytest.raises(FrontierEndpointError) as exc:
        await build_dispatch_body(req)
    assert exc.value.field == "dispatch_thread_id"


@pytest.mark.asyncio
async def test_team_dispatch_collapses_to_latest_user_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_hydrate(
        agent: str, transcript_id: str | None = None, **_k: Any
    ) -> HydrationBundle:
        return _bundle(AgentMeta(default_model="openai/gpt-5.4-mini"))

    monkeypatch.setattr(
        "systems.frontier_consult.service.hydrate_agent",
        fake_hydrate,
    )
    req = FrontierGenerateRequest(
        messages=[
            {"role": "user", "content": "old"},
            {"role": "assistant", "content": "stale"},
            {"role": "user", "content": "latest"},
        ],
        role="reviewer",
        dispatch_thread_id=_DISPATCH_THREAD,
    )
    body = await build_dispatch_body(req)
    assert body["messages"] == [{"role": "user", "content": "latest"}]


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        # grok-4.5 standard card admits client-side MCP tool loop.
        ("xai/grok-4.5", True),
        ("anthropic/claude-opus-4-8", True),
        ("openai/gpt-5.5", True),
        # Gemini is MCP-capable now (agents.yaml gemini/api tool_surface=mcp) —
        # no longer clamped. Two-surface-by-model-shape nuance is future work.
        ("google/gemini-3.5-flash", True),
        ("google/gemini-2.5-pro", True),
    ],
)
def test_mcp_enabled_for_team_dispatch_shared_client_loop_clamp(
    model: str, expected: bool
) -> None:
    from .admission import mcp_enabled_for_team_dispatch

    assert mcp_enabled_for_team_dispatch(model) is expected


@pytest.mark.parametrize(
    ("model", "caller_mcp", "expected"),
    [
        # Anthropic native model honors explicit caller inline intent (thread
        # 1653): caller_mcp=False → no MCP (→ remote_mcp default False → inline);
        # None keeps the team default (tools-on); True keeps tools-on.
        ("anthropic/claude-opus-4-8", False, False),
        ("anthropic/claude-opus-4-8", True, True),
        ("anthropic/claude-opus-4-8", None, True),
        ("anthropic/claude-opus-4-8", False, False),
        ("openai/gpt-5.5", False, False),
        ("openai/gpt-5.5", None, True),
        # grok-4.5 honors caller intent (MCP-capable standard card).
        ("xai/grok-4.5", True, True),
        ("xai/grok-4.5", False, False),
        ("xai/grok-4.5", None, True),
    ],
)
def test_mcp_enabled_for_team_dispatch_honors_caller_intent(
    model: str, caller_mcp: bool | None, expected: bool
) -> None:
    from .admission import mcp_enabled_for_team_dispatch

    assert mcp_enabled_for_team_dispatch(model, caller_mcp) is expected


def test_admission_uncarded_model_raises_structured_422() -> None:
    from .admission import FrontierEndpointError, mcp_enabled_for_team_dispatch

    events: list[Any] = []

    with pytest.raises(FrontierEndpointError) as exc:
        mcp_enabled_for_team_dispatch(
            "openai/gpt-4o",
            request_id="req-card-miss",
            event_publisher=events.append,
        )
    err = exc.value
    assert err.status_code == 422
    assert err.code == "capability_card_missing"
    assert err.field == "model"
    assert err.details is not None
    assert err.details["model"] == "openai/gpt-4o"
    assert err.details["reason_code"] == "capability_card_missing"
    assert events
    assert events[0].signal == "dispatch.capability.card.missing"


@pytest.mark.asyncio
async def test_build_dispatch_body_uncarded_model_emits_card_missing_event() -> None:
    events: list[Any] = []

    req = FrontierGenerateRequest(
        messages=[{"role": "user", "content": "x"}],
        role="reviewer",
        dispatch_thread_id=_DISPATCH_THREAD,
        model="openai/gpt-4o",
    )
    with pytest.raises(FrontierEndpointError) as exc:
        await build_dispatch_body(req, event_publisher=events.append)
    err = exc.value
    assert err.status_code == 422
    assert err.code == "capability_card_missing"
    assert err.field == "model"
    card_missing = [e for e in events if e.signal == "dispatch.capability.card.missing"]
    assert len(card_missing) == 1
    assert card_missing[0].payload["model"] == "openai/gpt-4o"


@pytest.mark.asyncio
async def test_build_dispatch_body_uncarded_skills_path() -> None:
    events: list[Any] = []

    req = FrontierGenerateRequest(
        messages=[{"role": "user", "content": "x"}],
        role="reviewer",
        dispatch_thread_id=_DISPATCH_THREAD,
        model="openai/gpt-4o",
        skills=["code_execution"],
    )
    with pytest.raises(FrontierEndpointError) as exc:
        await build_dispatch_body(req, event_publisher=events.append)
    assert exc.value.code == "capability_card_missing"
    card_missing = [e for e in events if e.signal == "dispatch.capability.card.missing"]
    assert len(card_missing) == 1


@pytest.mark.asyncio
async def test_build_dispatch_body_no_role_uncarded_returns_422() -> None:
    events: list[Any] = []

    req = FrontierGenerateRequest(
        messages=[{"role": "user", "content": "x"}],
        model="openai/gpt-4o",
    )
    with pytest.raises(FrontierEndpointError) as exc:
        await build_dispatch_body(req, event_publisher=events.append)
    assert exc.value.status_code == 422
    assert exc.value.code == "capability_card_missing"
    card_missing = [e for e in events if e.signal == "dispatch.capability.card.missing"]
    assert len(card_missing) == 1


@pytest.mark.asyncio
async def test_card_missing_precedes_skills_invalid() -> None:
    events: list[Any] = []

    req = FrontierGenerateRequest(
        messages=[{"role": "user", "content": "x"}],
        role="reviewer",
        dispatch_thread_id=_DISPATCH_THREAD,
        model="openai/gpt-4o",
        skills=["nonexistent-skill-id-xyz"],
    )
    with pytest.raises(FrontierEndpointError) as exc:
        await build_dispatch_body(req, event_publisher=events.append)
    assert exc.value.code == "capability_card_missing"
    assert exc.value.field == "model"


@pytest.mark.asyncio
async def test_gate_effective_model_parity_with_hydration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from .service import _resolve_pre_hydration_effective_model

    captured_hydrate_model: list[str | None] = []

    async def fake_hydrate(
        agent: str,
        transcript_id: str | None = None,
        *,
        model: str | None = None,
        **_k: Any,
    ) -> HydrationBundle:
        captured_hydrate_model.append(model)
        return _bundle(
            AgentMeta(
                default_model="openai/gpt-5.5",
                allowed_models=["openai/gpt-5.5", "anthropic/claude-opus-4-8"],
            ),
        )

    monkeypatch.setattr(
        "systems.frontier_consult.service.hydrate_agent",
        fake_hydrate,
    )

    explicit_req = FrontierGenerateRequest(
        messages=[{"role": "user", "content": "x"}],
        role="reviewer",
        dispatch_thread_id=_DISPATCH_THREAD,
        model="anthropic/claude-opus-4-8",
    )
    explicit_gate = _resolve_pre_hydration_effective_model(
        explicit_req, request_id="parity-explicit"
    )
    await build_dispatch_body(explicit_req)
    assert explicit_gate == "anthropic/claude-opus-4-8"
    assert captured_hydrate_model[-1] == "anthropic/claude-opus-4-8"

    role_default_req = FrontierGenerateRequest(
        messages=[{"role": "user", "content": "x"}],
        role="reviewer",
        dispatch_thread_id=_DISPATCH_THREAD,
    )
    role_default_gate = _resolve_pre_hydration_effective_model(
        role_default_req, request_id="parity-role-default"
    )
    body = await build_dispatch_body(role_default_req)
    assert body["pipeline_options"]["model"] == role_default_gate

    no_role_req = FrontierGenerateRequest(
        messages=[{"role": "user", "content": "x"}],
        model="anthropic/claude-opus-4-8",
    )
    no_role_gate = _resolve_pre_hydration_effective_model(
        no_role_req, request_id="parity-no-role"
    )
    no_role_body = await build_dispatch_body(no_role_req)
    assert no_role_body["pipeline_options"]["model"] == no_role_gate


@pytest.mark.asyncio
async def test_explicit_gemini_reviewer_admitted_caller_mcp_false_honored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gemini (mcp-capable) reviewer is admitted; an explicit caller mcp=False is
    honored at admission (thread 1653 knob), not a family inline-only clamp."""

    async def fake_hydrate(
        agent: str, transcript_id: str | None = None, **_k: Any
    ) -> HydrationBundle:
        return _bundle(
            AgentMeta(
                default_model="openai/gpt-5.5",
                allowed_models=["openai/gpt-5.5"],
                allowed_options=None,
                capability_tier="inline-only",
            ),
        )

    monkeypatch.setattr(
        "systems.frontier_consult.service.hydrate_agent",
        fake_hydrate,
    )

    req = FrontierGenerateRequest(
        messages=[{"role": "user", "content": "x"}],
        role="reviewer",
        dispatch_thread_id=_DISPATCH_THREAD,
        model="google/gemini-2.5-pro",
        mcp=False,
    )
    body = await build_dispatch_body(req)
    assert body["pipeline_options"]["model"] == "google/gemini-2.5-pro"
    assert body["pipeline_options"]["mcp"] is False


@pytest.mark.asyncio
async def test_gemini_reviewer_team_default_mcp_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gemini reviewer with the team default (caller omits mcp) is tools-on now
    that gemini is MCP-capable — the prior inline-only clamp is gone."""

    async def fake_hydrate(
        agent: str, transcript_id: str | None = None, **_k: Any
    ) -> HydrationBundle:
        return _bundle(
            AgentMeta(
                default_model="openai/gpt-5.5",
                allowed_models=["openai/gpt-5.5"],
                allowed_options=None,
                capability_tier="inline-only",
            ),
        )

    monkeypatch.setattr(
        "systems.frontier_consult.service.hydrate_agent",
        fake_hydrate,
    )

    req = FrontierGenerateRequest(
        messages=[{"role": "user", "content": "x"}],
        role="reviewer",
        dispatch_thread_id=_DISPATCH_THREAD,
        model="google/gemini-2.5-pro",
    )
    body = await build_dispatch_body(req)
    assert body["pipeline_options"]["mcp"] is True


@pytest.mark.asyncio
async def test_required_criticality_fails_closed_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_hydrate(
        agent: str, transcript_id: str | None = None, **_k: Any
    ) -> HydrationBundle:
        return HydrationBundle(
            briefing_card_md="# briefing",
            agent_meta=AgentMeta(default_model="xai/grok-4.5"),
            inline_only=True,
            required_body_unresolved=True,
            injection_meta={
                "dropped": [{"id": "rule:critical", "reason": "body_missing"}]
            },
        )

    monkeypatch.setattr(
        "systems.frontier_consult.service.hydrate_agent",
        fake_hydrate,
    )

    req = FrontierGenerateRequest(
        messages=[{"role": "user", "content": "x"}],
        role="grok-api-multi",
        dispatch_thread_id=_DISPATCH_THREAD,
    )
    with pytest.raises(FrontierEndpointError) as exc:
        await build_dispatch_body(req)
    assert exc.value.field == "injected_bodies"


@pytest.mark.asyncio
async def test_event_emitted_enriched(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[Any] = []

    async def fake_hydrate(
        agent: str, transcript_id: str | None = None, **_k: Any
    ) -> HydrationBundle:
        return HydrationBundle(
            briefing_card_md="# briefing",
            agent_meta=AgentMeta(default_model="xai/grok-4.5"),
            inline_only=True,
            injected_bodies_md="<!-- injected-body:rule:foo digest:sha256:abc -->",
            injection_meta={
                "injected": [{"id": "rule:foo", "digest": "sha256:abc", "bytes": 42}],
                "dropped": [{"id": "rule:bar", "reason": "budget"}],
                "metrics": {
                    "cache_hit": True,
                    "cold_fetches": 1,
                    "elapsed_ms": 12,
                    "deadline_hit": False,
                },
            },
        )

    monkeypatch.setattr(
        "systems.frontier_consult.service.hydrate_agent",
        fake_hydrate,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.service.enforce_model",
        lambda **_k: None,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.service.enforce_options",
        lambda **_k: None,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.service.enforce_team_dispatch_generate_admit",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.service.resolve_wire_model_id",
        lambda model, **_: type("R", (), {"wire_id": model})(),
    )
    monkeypatch.setattr(
        "systems.frontier_consult.service.canonical_model_entity_id",
        lambda _m: "model:test",
    )
    monkeypatch.setattr(
        "systems.frontier_consult.service.is_chat_completions_only",
        lambda _m: False,
    )

    req = FrontierGenerateRequest(
        messages=[{"role": "user", "content": "x"}],
        role="grok-api-multi",
        dispatch_thread_id=_DISPATCH_THREAD,
        model="xai/grok-4.5",
    )
    await build_dispatch_body(req, event_publisher=events.append)

    inline_events = [e for e in events if e.signal == "dispatch.skills.inline.resolved"]
    assert len(inline_events) == 1
    payload = inline_events[0].payload
    assert payload["injected"]
    assert payload["dropped"][0]["reason"] == "budget"
    assert payload["total_bytes"] == 42
    assert payload["budget_bytes"] == 50000
    assert payload["cache_hit"] is True
    assert payload["cold_fetches"] == 1
    assert payload["elapsed_ms"] == 12
    assert payload["deadline_hit"] is False


@pytest.mark.asyncio
async def test_build_dispatch_body_omits_resolved_contract_from_pipeline_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """resolved_contract is dispatch metadata (outer body), not a runtime option.

    Regression: api_role_generate's op=generate path set resolved_contract on the
    request; build_dispatch_body leaked it into pipeline_options, which the
    team-dispatch handler hard-rejects via reject_unknown_runtime_options. The fix
    keeps it on the outer body only. See agent-bus:1731.
    """

    async def fake_hydrate(
        agent: str, transcript_id: str | None = None, **_k: Any
    ) -> HydrationBundle:
        return _bundle(
            AgentMeta(
                default_model="openai/gpt-5.5",
                allowed_models=[],
                allowed_options=None,
            ),
        )

    monkeypatch.setattr(
        "systems.frontier_consult.service.hydrate_agent",
        fake_hydrate,
    )

    req = FrontierGenerateRequest(
        messages=[{"role": "user", "content": "x"}],
        role="reviewer",
        dispatch_thread_id=_DISPATCH_THREAD,
        output_contract="thread",
        op="to_thread",
        resolved_contract="light-bounded",
        # target_thread omitted so verify_thread_writable is skipped
        # (guard is output_contract == "thread" and target_thread).
    )
    body = await build_dispatch_body(req)

    assert body["resolved_contract"] == "light-bounded"
    assert "resolved_contract" not in body["pipeline_options"]
    assert body["pipeline_options"]["output_contract"] == "thread"


@pytest.mark.asyncio
async def test_build_dispatch_body_pipeline_options_within_handler_accepted_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Class guard: emitted pipeline_options must stay within the handler's
    accepted runtime-option key set after route._dispatch pops the response-only
    preview keys. Catches the whole leak class, not just resolved_contract.
    See agent-bus:1731.
    """
    from systems.pipeline.core.handlers.frontier_dispatch.handler import (
        FrontierDispatchHandler,
    )

    async def fake_hydrate(
        agent: str, transcript_id: str | None = None, **_k: Any
    ) -> HydrationBundle:
        return _bundle(
            AgentMeta(
                default_model="openai/gpt-5.5",
                allowed_models=[],
                allowed_options=None,
            ),
        )

    monkeypatch.setattr(
        "systems.frontier_consult.service.hydrate_agent",
        fake_hydrate,
    )

    req = FrontierGenerateRequest(
        messages=[{"role": "user", "content": "x"}],
        role="reviewer",
        dispatch_thread_id=_DISPATCH_THREAD,
        output_contract="thread",
        op="to_thread",
        resolved_contract="light-bounded",
    )
    body = await build_dispatch_body(req)
    po = dict(body["pipeline_options"])
    # Replay route._dispatch sanitization: response-only previews are popped
    # before the body is forwarded to the pipeline.
    po.pop("_knob_resolution_preview", None)
    po.pop("_capability_preview", None)
    leaked = (
        set(po) - {"stream"} - FrontierDispatchHandler._ACCEPTED_RUNTIME_OPTION_KEYS
    )
    assert leaked == set(), (
        f"pipeline_options leaked non-accepted keys: {sorted(leaked)}"
    )


@pytest.mark.parametrize(
    ("role", "resolved_contract", "generation_options", "expect_sentinel"),
    [
        ("cursor-sdk", None, None, True),
        ("reviewer", "implement", None, True),
        ("reviewer", None, {"coding_session": True}, True),
        ("reviewer", None, None, False),
        ("reviewer", "light-bounded", None, False),
    ],
)
@pytest.mark.asyncio
async def test_code_touching_invariant_injection_predicate(
    monkeypatch: pytest.MonkeyPatch,
    role: str,
    resolved_contract: str | None,
    generation_options: dict[str, Any] | None,
    expect_sentinel: bool,
) -> None:
    async def fake_hydrate(
        agent: str,
        transcript_id: str | None = None,
        *,
        code_touching: bool = False,
        inject_profile: str | None = None,
        **_k: Any,
    ) -> HydrationBundle:
        injected = ""
        if code_touching:
            injected = (
                "\n\n<!-- cortex:invariant-skills-autoappend sha256=abc count=2 -->"
                "\narchitecture invariants body\nulg architecture body"
            )
        return HydrationBundle(
            briefing_card_md="# briefing",
            agent_meta=AgentMeta(default_model="openai/gpt-5.5"),
            inline_only=False,
            injected_bodies_md=injected or None,
            injection_meta={"injected": [{"bytes": 10}], "dropped": [], "metrics": {}},
        )

    monkeypatch.setattr(
        "systems.frontier_consult.service.hydrate_agent",
        fake_hydrate,
    )

    req = FrontierGenerateRequest(
        messages=[{"role": "user", "content": "x"}],
        role=role,
        dispatch_thread_id=_DISPATCH_THREAD,
        resolved_contract=resolved_contract,
        generation_options=generation_options,
    )
    body = await build_dispatch_body(req)
    system = body["pipeline_options"]["system"]
    has_sentinel = "cortex:invariant-skills-autoappend" in system
    assert has_sentinel is expect_sentinel
    assert _code_touching_generate(req) is expect_sentinel


@pytest.mark.asyncio
async def test_inline_only_uses_hydrate_injected_bodies_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_hydrate(
        agent: str, transcript_id: str | None = None, **_k: Any
    ) -> HydrationBundle:
        return HydrationBundle(
            briefing_card_md="# briefing",
            agent_meta=AgentMeta(default_model="xai/grok-4.5"),
            inline_only=True,
            injected_bodies_md="<!-- injected-body:rule:foo digest:sha256:abc -->",
            injection_meta={"injected": [{"bytes": 1}], "dropped": [], "metrics": {}},
        )

    monkeypatch.setattr(
        "systems.frontier_consult.service.hydrate_agent",
        fake_hydrate,
    )

    req = FrontierGenerateRequest(
        messages=[{"role": "user", "content": "x"}],
        role="cursor-sdk",
        dispatch_thread_id=_DISPATCH_THREAD,
    )
    body = await build_dispatch_body(req)
    system = body["pipeline_options"]["system"]
    assert "cortex:invariant-skills-autoappend" not in system
    assert "injected-body:rule:foo" in system


@pytest.mark.asyncio
async def test_build_dispatch_body_carries_skills_mount(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_hydrate(
        agent: str, transcript_id: str | None = None, **_k: Any
    ) -> HydrationBundle:
        return _bundle(
            AgentMeta(
                default_model="openai/gpt-5.5",
                allowed_models=["openai/gpt-5.5"],
            ),
        )

    monkeypatch.setattr(
        "systems.frontier_consult.service.hydrate_agent",
        fake_hydrate,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.service.resolve_skill_bundles",
        lambda skill_ids, **_: [
            ResolvedSkillBundle(
                canonical_slug="agent-identity-signoff",
                description="Identity sign-off discipline.",
                data_base64="UEsDBBQAAAAI",
            )
        ],
    )

    req = FrontierGenerateRequest(
        messages=[{"role": "user", "content": "hello"}],
        role="reviewer",
        dispatch_thread_id=_DISPATCH_THREAD,
        model="openai/gpt-5.5",
        skills=["agent-identity-signoff"],
        resolved_contract="light-bounded",
    )
    events: list[Any] = []

    body = await build_dispatch_body(req, event_publisher=events.append)
    mount = body["pipeline_options"]["skills_mount"]
    assert mount == [
        {
            "name": "agent-identity-signoff",
            "description": "Identity sign-off discipline.",
            "data_base64": "UEsDBBQAAAAI",
        }
    ]
    assert any(evt.signal == "dispatch.skills.mounted" for evt in events)


@pytest.mark.asyncio
async def test_skills_non_openai_admits_layer_a_fs_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_hydrate(
        agent: str, transcript_id: str | None = None, **_k: Any
    ) -> HydrationBundle:
        return _bundle(
            AgentMeta(
                default_model="anthropic/claude-opus-4-8",
                allowed_models=["anthropic/claude-opus-4-8"],
            ),
        )

    monkeypatch.setattr(
        "systems.frontier_consult.service.hydrate_agent",
        fake_hydrate,
    )

    req = FrontierGenerateRequest(
        messages=[{"role": "user", "content": "hello"}],
        role="reviewer",
        dispatch_thread_id=_DISPATCH_THREAD,
        model="anthropic/claude-opus-4-8",
        skills=["architecture-invariants"],
        resolved_contract="light-bounded",
    )
    events: list[Any] = []
    body = await build_dispatch_body(req, event_publisher=events.append)
    system = body["pipeline_options"]["system"]
    assert 'fs(sandbox="workspaces"' in system
    assert "architecture-invariants.md" in system
    channel_events = [
        evt for evt in events if evt.signal == "dispatch.skills.channel.resolved"
    ]
    assert len(channel_events) == 1
    row = channel_events[0].payload["skills"][0]
    assert row["channel"] == "layer_a"
    assert row["origin"] == "caller"
    assert "skills_mount" not in body["pipeline_options"]


@pytest.mark.asyncio
async def test_skills_unknown_id_rejects_before_pipeline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_hydrate(
        agent: str, transcript_id: str | None = None, **_k: Any
    ) -> HydrationBundle:
        return _bundle(
            AgentMeta(
                default_model="openai/gpt-5.5",
                allowed_models=["openai/gpt-5.5"],
            ),
        )

    monkeypatch.setattr(
        "systems.frontier_consult.service.hydrate_agent",
        fake_hydrate,
    )

    req = FrontierGenerateRequest(
        messages=[{"role": "user", "content": "hello"}],
        role="reviewer",
        dispatch_thread_id=_DISPATCH_THREAD,
        model="openai/gpt-5.5",
        skills=["definitely-not-a-skill"],
        resolved_contract="light-bounded",
    )
    with pytest.raises(FrontierEndpointError) as exc:
        await build_dispatch_body(req)
    assert exc.value.field == "skills"
    assert "definitely-not-a-skill" in exc.value.reason


@pytest.mark.asyncio
async def test_api_role_generate_forwards_skills_and_emits_dispatch_skills_mounted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_hydrate(
        agent: str, transcript_id: str | None = None, **_k: Any
    ) -> HydrationBundle:
        return _bundle(
            AgentMeta(
                default_model="openai/gpt-5.5",
                allowed_models=["openai/gpt-5.5"],
            ),
        )

    monkeypatch.setattr(
        "systems.frontier_consult.service.hydrate_agent",
        fake_hydrate,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.service.resolve_skill_bundles",
        lambda skill_ids, **_: [
            ResolvedSkillBundle(
                canonical_slug="agent-identity-signoff",
                description="Identity sign-off discipline.",
                data_base64="UEsDBBQAAAAI",
            )
        ],
    )

    body = TeamDispatchGenerateBody(
        op="generate",
        role="reviewer",
        dispatch_thread_id=_DISPATCH_THREAD,
        contract="light-bounded",
        model="openai/gpt-5.5",
        skills=["agent-identity-signoff"],
        caller_agent="cursor",
    )
    response = Response()
    events: list[Any] = []
    mock_profile = type("Profile", (), {"tool_surface": "mcp"})()

    async def capture_dispatch(
        req: FrontierGenerateRequest,
        _response: Response,
    ) -> dict[str, Any]:
        assert req.skills == ["agent-identity-signoff"]
        dispatch_body = await build_dispatch_body(req, event_publisher=events.append)
        mount = dispatch_body["pipeline_options"]["skills_mount"]
        assert mount == [
            {
                "name": "agent-identity-signoff",
                "description": "Identity sign-off discipline.",
                "data_base64": "UEsDBBQAAAAI",
            }
        ]
        return {
            "execution_id": "exec-skills",
            "status": "running",
            "knob_resolution": {"resolved_model": "openai/gpt-5.5"},
        }

    with (
        patch(
            "systems.frontier_consult.api_role_generate.create_handoff_thread",
            new_callable=AsyncMock,
            return_value="1602",
        ),
        patch(
            "systems.frontier_consult.route._dispatch",
            side_effect=capture_dispatch,
        ),
        patch(
            "systems.frontier_consult.api_role_generate._resolve_role_profile",
            return_value=("reviewer", "openai", "api", mock_profile),
        ),
        patch(
            "systems.frontier_consult.api_role_generate.read_latest_dispatch_thread_body",
            new_callable=AsyncMock,
            return_value="hello",
        ),
    ):
        result = await dispatch_api_role_generate(
            request_id="req-skills",
            body=body,
            response=response,
        )

    assert isinstance(result, dict)
    assert result["execution_id"] == "exec-skills"
    mounted = [evt for evt in events if evt.signal == "dispatch.skills.mounted"]
    assert len(mounted) == 1
    payload = mounted[0].payload
    assert payload["role"] == "reviewer"
    assert payload["model"] == "openai/gpt-5.5"
    assert payload["canonical_slugs"] == ["agent-identity-signoff"]


@pytest.mark.asyncio
async def test_api_role_generate_skills_non_openai_admits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_hydrate(
        agent: str, transcript_id: str | None = None, **_k: Any
    ) -> HydrationBundle:
        return _bundle(
            AgentMeta(
                default_model="anthropic/claude-opus-4-8",
                allowed_models=["anthropic/claude-opus-4-8"],
            ),
        )

    monkeypatch.setattr(
        "systems.frontier_consult.service.hydrate_agent",
        fake_hydrate,
    )

    body = TeamDispatchGenerateBody(
        op="generate",
        role="reviewer",
        dispatch_thread_id=_DISPATCH_THREAD,
        contract="light-bounded",
        model="anthropic/claude-opus-4-8",
        skills=["architecture-invariants"],
        caller_agent="cursor",
    )
    response = Response()
    mock_profile = type("Profile", (), {"tool_surface": "mcp"})()

    async def admit_non_openai_skills(
        req: FrontierGenerateRequest,
        _response: Response,
    ) -> dict[str, Any]:
        assert req.skills == ["architecture-invariants"]
        dispatch_body = await build_dispatch_body(req)
        system = dispatch_body["pipeline_options"]["system"]
        assert "architecture-invariants.md" in system
        return {
            "execution_id": "exec-skills-non-openai",
            "status": "running",
            "knob_resolution": {"resolved_model": "anthropic/claude-opus-4-8"},
        }

    with (
        patch(
            "systems.frontier_consult.api_role_generate.create_handoff_thread",
            new_callable=AsyncMock,
            return_value="1602",
        ),
        patch(
            "systems.frontier_consult.route._dispatch",
            side_effect=admit_non_openai_skills,
        ),
        patch(
            "systems.frontier_consult.api_role_generate._resolve_role_profile",
            return_value=("reviewer", "anthropic", "api", mock_profile),
        ),
        patch(
            "systems.frontier_consult.api_role_generate.read_latest_dispatch_thread_body",
            new_callable=AsyncMock,
            return_value="hello",
        ),
    ):
        result = await dispatch_api_role_generate(
            request_id="req-skills-non-openai",
            body=body,
            response=response,
        )

    assert result["execution_id"] == "exec-skills-non-openai"


class _CollectingEventBus:
    def __init__(self) -> None:
        self.events: list[Any] = []

    async def publish_nowait(self, event: Any) -> None:
        self.events.append(event)


@pytest.mark.asyncio
async def test_skills_dispatch_correlates_endpoint_request_id_on_lifecycle_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_hydrate(
        agent: str, transcript_id: str | None = None, **_k: Any
    ) -> HydrationBundle:
        return _bundle(
            AgentMeta(
                default_model="openai/gpt-5.5",
                allowed_models=["openai/gpt-5.5"],
            ),
        )

    monkeypatch.setattr(
        "systems.frontier_consult.service.hydrate_agent",
        fake_hydrate,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.service.resolve_skill_bundles",
        lambda skill_ids, **_: [
            ResolvedSkillBundle(
                canonical_slug="agent-identity-signoff",
                description="Identity sign-off discipline.",
                data_base64="UEsDBBQAAAAI",
            )
        ],
    )

    req = FrontierGenerateRequest(
        messages=[{"role": "user", "content": "hello"}],
        role="reviewer",
        dispatch_thread_id=_DISPATCH_THREAD,
        model="openai/gpt-5.5",
        skills=["agent-identity-signoff"],
        resolved_contract="light-bounded",
    )
    endpoint_events: list[Any] = []
    body = await build_dispatch_body(req, event_publisher=endpoint_events.append)
    mounted = [
        evt for evt in endpoint_events if evt.signal == "dispatch.skills.mounted"
    ]
    assert len(mounted) == 1
    request_id = mounted[0].payload["request_id"]
    endpoint_request_id = body["pipeline_options"]["_endpoint_request_id"]
    assert endpoint_request_id == request_id

    bus = _CollectingEventBus()
    tracker = PipelineExecutionTracker(event_bus=bus)
    record = tracker.register_execution(
        execution_id="exec-skills-correlation",
        pipeline="frontier-dispatch",
        started_at="2026-07-05T00:00:00Z",
        endpoint_request_id=endpoint_request_id,
    )
    await asyncio.sleep(0)

    assert record.endpoint_request_id == request_id
    async_events = [
        evt for evt in bus.events if evt.signal == "pipeline.dispatch.async"
    ]
    assert len(async_events) == 1
    lifecycle = async_events[0].payload
    assert lifecycle["execution_id"] == "exec-skills-correlation"
    assert lifecycle["endpoint_request_id"] == request_id
