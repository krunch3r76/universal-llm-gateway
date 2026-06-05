"""Tests for frontier consult admission service."""

from __future__ import annotations

from typing import Any

import pytest
from agent_seat import AgentMeta, HydrationBundle

from .service import (
    FrontierEndpointError,
    FrontierGenerateRequest,
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
    assert options["model"] == "openai/gpt-5.4-mini"
    assert options["model_entity_id"] == "model:gpt-5.4-mini"
    assert "tools" not in options
    assert options["mcp"] is True
    assert options["_endpoint_request_id"]


@pytest.mark.asyncio
async def test_explicit_model_override_can_fill_any_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_hydrate(
        agent: str, transcript_id: str | None = None, **_k: Any
    ) -> HydrationBundle:
        return _bundle(
            AgentMeta(
                default_model="xai/grok-4.20-multi-agent-0309",
                allowed_models=["xai/grok-4.20-multi-agent-0309"],
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
# either; tool surface is derived from the agent provider (xAI agents →
# mcp=False, all others → mcp=True). See
# test_team_dispatch_xai_agent_auto_suppresses_mcp +
# test_team_dispatch_non_xai_agent_enables_mcp.


@pytest.mark.asyncio
async def test_team_dispatch_xai_agent_auto_suppresses_mcp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """xAI team roles (skeptic, artisan) get mcp=False auto-derived — no caller knob."""

    async def fake_hydrate(
        agent: str, transcript_id: str | None = None, **_k: Any
    ) -> HydrationBundle:
        return _bundle(
            AgentMeta(
                default_model="xai/grok-4.20-multi-agent-0309",
                allowed_models=["xai/grok-4.20-multi-agent-0309"],
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
    assert body["pipeline_options"]["mcp"] is False


@pytest.mark.asyncio
async def test_team_dispatch_non_xai_agent_enables_mcp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-xAI team roles (gatherer, synthesizer, reviewer) get mcp=True auto-derived."""

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
                default_model="openai/gpt-5.4-mini",
                allowed_models=["openai/gpt-5.4-mini"],
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
    assert body["pipeline_options"]["model"] == "openai/gpt-5.4-mini"
    assert body["pipeline_options"]["model_entity_id"] == "model:gpt-5.4-mini"


@pytest.mark.asyncio
async def test_request_id_is_propagated_and_persona_free_mcp_defaults_false() -> None:
    """Persona-free dispatch defaults to mcp=False (one-shot reasoning)."""
    req = FrontierGenerateRequest(
        messages=[{"role": "user", "content": "x"}],
        model="openai/gpt-5.4-mini",
    )
    body = await build_dispatch_body(req)
    assert body["model"] == "frontier-dispatch"
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


@pytest.mark.parametrize(
    ("model", "caller_mcp", "expected"),
    [
        ("google/gemini-3.5-flash", True, False),
        ("google/gemini-3.5-flash", None, False),
        ("google/gemini-2.5-pro", True, False),
        ("xai/grok-4.20-multi-agent-0309", True, False),
        ("xai/grok-4.20-multi-agent-0309", None, False),
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
async def test_persona_free_gemini_mcp_true_clamped_false() -> None:
    """Inline-only gemini: persona-free frontier HTTP clamps mcp=True to False at admission."""
    req = FrontierGenerateRequest(
        messages=[{"role": "user", "content": "x"}],
        model="google/gemini-3.5-flash",
        mcp=True,
    )
    body = await build_dispatch_body(req)
    assert body["model"] == "frontier-dispatch"
    assert body["pipeline_options"]["mcp"] is False


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
async def test_max_tool_turns_omitted_does_not_set_key() -> None:
    """Handler default of 10 fires when caller omits max_tool_turns."""
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
async def test_rejects_chat_completions_only_model_exact() -> None:
    """Exact frozenset entry rejected before dispatch."""
    req = FrontierGenerateRequest(
        messages=[{"role": "user", "content": "x"}],
        model="openai/gpt-5-search-api",
    )
    with pytest.raises(FrontierEndpointError) as exc:
        await build_dispatch_body(req)
    assert exc.value.field == "model"
    assert "Chat Completions-only" in exc.value.reason
    assert "llm_generate" in exc.value.reason


@pytest.mark.asyncio
async def test_rejects_chat_completions_only_model_suffix_variant() -> None:
    """Suffix-matched future *-search-api variants are rejected."""
    req = FrontierGenerateRequest(
        messages=[{"role": "user", "content": "x"}],
        model="openai/gpt-6-search-api",
    )
    with pytest.raises(FrontierEndpointError) as exc:
        await build_dispatch_body(req)
    assert exc.value.field == "model"
    assert "Chat Completions-only" in exc.value.reason


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
        # Non-multi-agent grok now gets the MCP tool loop (stale blanket
        # provider!="xai" flatten removed).
        ("xai/grok-4.3", True),
        ("xai/grok-4.20-0309-reasoning", True),
        # Multi-agent xAI still rejects client-side tools.
        ("xai/grok-4.20-multi-agent-0309", False),
        ("anthropic/claude-opus-4-8", True),
        ("openai/gpt-5.5", True),
        ("google/gemini-3.5-flash", False),
        ("google/gemini-2.5-pro", False),
    ],
)
def test_mcp_enabled_for_team_dispatch_shared_client_loop_clamp(
    model: str, expected: bool
) -> None:
    from .admission import mcp_enabled_for_team_dispatch

    assert mcp_enabled_for_team_dispatch(model) is expected


@pytest.mark.asyncio
async def test_explicit_gemini_reviewer_admitted_with_mcp_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Guard 1: inline-only effective model is admitted; MCP suppressed at admission."""

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
    assert body["pipeline_options"]["model"] == "google/gemini-2.5-pro"
    assert body["pipeline_options"]["mcp"] is False
