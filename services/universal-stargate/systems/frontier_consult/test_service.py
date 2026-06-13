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
async def test_build_dispatch_body_sets_knob_resolution_preview(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_hydrate(
        agent: str, transcript_id: str | None = None, **_k: Any
    ) -> HydrationBundle:
        return _bundle(
            AgentMeta(
                default_model="openai/gpt-5.5",
                allowed_models=["openai/gpt-5.5"],
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
                default_model="openai/gpt-5.5",
                allowed_models=["openai/gpt-5.5"],
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
    caps = body["pipeline_options"]["_capability_preview"]
    assert caps["role"] == "skeptic"
    assert caps["inline_only"] is True
    assert caps["mcp_enabled"] is False
    assert caps["resolved_model"] == "xai/grok-4.20-multi-agent-0309"


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
async def test_persona_free_gemini_mcp_true_honored() -> None:
    """Gemini is MCP-capable: persona-free frontier honors caller mcp=True (no clamp)."""
    req = FrontierGenerateRequest(
        messages=[{"role": "user", "content": "x"}],
        model="google/gemini-3.5-flash",
        mcp=True,
    )
    body = await build_dispatch_body(req)
    assert body["model"] == "frontier-dispatch"
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
async def test_max_tool_turns_omitted_role_defaults_to_100() -> None:
    """Team-dispatch role consults default to 100 tool turns when omitted."""
    req = FrontierGenerateRequest(
        messages=[{"role": "user", "content": "x"}],
        role="reviewer",
        dispatch_thread_id="dispatch-1",
        model="openai/gpt-5.4-mini",
    )
    body = await build_dispatch_body(req)
    assert body["pipeline_options"]["max_tool_turns"] == 100


@pytest.mark.asyncio
async def test_max_tool_turns_omitted_persona_free_defers_to_handler() -> None:
    """Persona-free frontier omits max_tool_turns; handler default is 100."""
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
        ("anthropic/claude-fable-5", False, False),
        ("anthropic/claude-fable-5", True, True),
        ("anthropic/claude-fable-5", None, True),
        ("anthropic/claude-opus-4-8", False, False),
        ("openai/gpt-5.5", False, False),
        ("openai/gpt-5.5", None, True),
        # Inline-only / no-client-tool families stay clamped to False regardless
        # of caller intent (gemini clamp is covered by the catalog-backed
        # shared_client_loop_clamp test; xai multi-agent clamps without catalog).
        ("xai/grok-4.20-multi-agent-0309", True, False),
    ],
)
def test_mcp_enabled_for_team_dispatch_honors_caller_intent(
    model: str, caller_mcp: bool | None, expected: bool
) -> None:
    from .admission import mcp_enabled_for_team_dispatch

    assert mcp_enabled_for_team_dispatch(model, caller_mcp) is expected


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
            agent_meta=AgentMeta(default_model="xai/grok-4.3-multi-agent"),
            inline_only=True,
            required_body_unresolved=True,
            injection_meta={"dropped": [{"id": "rule:critical", "reason": "body_missing"}]},
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
            agent_meta=AgentMeta(default_model="xai/grok-4.3-multi-agent"),
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
        model="xai/grok-4.3-multi-agent",
    )
    await build_dispatch_body(req, event_publisher=events.append)

    inline_events = [e for e in events if e.signal == "inline.body.injection.resolved"]
    assert len(inline_events) == 1
    payload = inline_events[0].payload
    assert payload["injected"]
    assert payload["dropped"][0]["reason"] == "budget"
    assert payload["total_bytes"] == 42
    assert payload["budget_bytes"] == 24000
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
