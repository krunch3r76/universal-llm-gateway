"""Tests for the ``frontier_dispatch_v1`` pipeline handler.

Focused unit tests that mock ``run_native_tool_loop`` and ``hydrate_agent``;
verifies handler orchestration (persona vs persona-free mode, hydrated event
gating, tool event translation, missing-model error, cancel-check wiring).

Intentionally lightweight: stubs the PipelineContext/StepConfig surface
(handler only touches a few fields) instead of constructing the full
pydantic / dataclass graph.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import agent_seat
import pytest

from systems.pipeline.core.execution.errors import FrontierDispatchExhaustedError
from systems.pipeline.core.handlers.frontier_dispatch import (
    FrontierDispatchHandler,
)
from systems.pipeline.core.handlers.frontier_dispatch import (
    native_loop as fd_native_mod,
)
from systems.pipeline.core.handlers.frontier_dispatch_tools import XAI_BUILTIN_TOOLS

_TEST_MODEL_ENTITY_ID = "model:test-slug"


class _FakeStep:
    """Minimal StepConfig surface used by the handler."""

    def __init__(
        self,
        *,
        name: str = "respond",
        type: str = "frontier_dispatch_v1",
        model_ref: str | None = None,
        model_requirements: dict[str, Any] | None = None,
        resolved_async_model: str | None = None,
        system_prompt: str | None = None,
        generation_parameters: dict[str, Any] | None = None,
        domain_fields: dict[str, Any] | None = None,
    ) -> None:
        self.name = name
        self.type = type
        self.model_ref = model_ref
        self.model_requirements = model_requirements
        self._resolved_async_model = resolved_async_model
        self.system_prompt = system_prompt
        self.generation_parameters = generation_parameters or {}
        self.handler_inputs: dict[str, Any] = {}
        self._domain = domain_fields or {}

    @property
    def id(self) -> str:
        return self.name

    def get_domain_field(self, key: str, default: Any = None) -> Any:
        return self._domain.get(key, default)

    async def get_target_model_id_async(
        self,
        registry: Any,
        *,
        domain: str | None = None,
        search_path: str | None = None,
        model_ref_overrides: dict[str, str] | None = None,
        context: Any | None = None,
    ) -> str | None:
        """Mirror ``StepConfig.get_target_model_id_async`` for handler tests.

        - ``model_ref`` set → returns it (registry lookup is irrelevant for
          provider-prefixed ids; production passes through on KeyError).
        - ``model_requirements`` set without ``model_ref`` → returns the
          ``resolved_async_model`` the test pre-stubs (simulates
          ``get_ranked_candidates`` selecting a candidate).
        - Otherwise → ``None`` (resolution failure).
        """
        if self.model_ref:
            return self.model_ref
        if self.model_requirements:
            return self._resolved_async_model
        return self._resolved_async_model


def _make_context(
    *,
    options: dict[str, Any] | None = None,
    source_text: str = "what's up?",
    messages: list[dict[str, Any]] | None = None,
    runtime_options: dict[str, Any] | None = None,
) -> SimpleNamespace:
    """Return a stub PipelineContext covering the fields the handler touches."""
    return SimpleNamespace(
        execution_id="exec-test-0001",
        source_text=source_text,
        messages=messages,
        options=options or {},
        runtime_options=runtime_options or {},
        _registry=None,
        pipeline=SimpleNamespace(domain=None, source_search_path=None),
        _proxy=SimpleNamespace(pipeline_dispatch_tracker=None, event_bus=None),
    )


class _FakeBundle:
    def __init__(self, *, capability_tier: str | None = None) -> None:
        from agent_seat.hydration import AgentMeta

        self.briefing_card_md = "# Briefing"
        self.continuation_md = None
        self.section_counts = {"briefing_bytes": 42, "todos": 3}
        self.continuation_id = None
        self.agent_meta = AgentMeta(capability_tier=capability_tier)
        self.inline_only = capability_tier == "inline-only"


class _FakeLoopResult:
    def __init__(
        self,
        *,
        content: str = "final text",
        tool_calls: list[Any] | None = None,
        exhausted: bool = False,
        cancelled: bool = False,
        turns_used: int = 1,
        provider: str = "anthropic",
    ) -> None:
        self.content = content
        self.reasoning = None
        self.tool_calls = tool_calls or []
        self.turns_used = turns_used
        self.exhausted = exhausted
        self.cancelled = cancelled
        self.usage = {"input_tokens": 10, "output_tokens": 5}
        self.finish_reason = "end_turn"
        self.block_reason = None
        self.provider = provider
        self.raw = {}
        self.tool_calls_made = len(self.tool_calls)


@pytest.fixture
def published_events() -> list[Any]:
    return []


@pytest.fixture
def handler(
    monkeypatch: pytest.MonkeyPatch, published_events: list[Any]
) -> FrontierDispatchHandler:
    h = FrontierDispatchHandler()

    def _capture(_ctx: Any, event: Any) -> None:
        published_events.append(event)

    monkeypatch.setattr(h, "_publish_bus_event", _capture)
    return h


@pytest.mark.asyncio
async def test_handler_team_mode_fires_hydrated_event(
    handler: FrontierDispatchHandler,
    monkeypatch: pytest.MonkeyPatch,
    published_events: list[Any],
) -> None:
    async def fake_hydrate(
        agent: str, transcript_id: str | None = None, **_k: Any
    ) -> _FakeBundle:
        return _FakeBundle()

    def fake_assemble(agent: str, **_k: Any) -> str:
        return f"SYSTEM[{agent}]"

    async def fake_loop(**_k: Any) -> _FakeLoopResult:
        return _FakeLoopResult(provider="xai")

    monkeypatch.setattr(agent_seat, "hydrate_agent", fake_hydrate)
    monkeypatch.setattr(agent_seat, "assemble_system_prompt", fake_assemble)
    monkeypatch.setattr(fd_native_mod, "run_native_tool_loop", fake_loop)

    step = _FakeStep()
    context = _make_context(
        options={"model": "xai/grok-4.20-multi-agent-0309", "role": "skeptic"},
    )

    out = await handler.execute(step, context)

    signals = [e.signal for e in published_events]
    assert "pipeline.frontier.dispatch.hydrated" in signals
    assert "pipeline.frontier.dispatch.completed" in signals
    assert out.system_prompt.endswith("SYSTEM[skeptic]")
    assert out.json["hydration"]["agent"] == "skeptic"
    assert out.json["provider"] == "xai"


@pytest.mark.asyncio
async def test_handler_persona_free_mode_skips_hydration(
    handler: FrontierDispatchHandler,
    monkeypatch: pytest.MonkeyPatch,
    published_events: list[Any],
) -> None:
    hydrate_calls: list[str] = []

    async def fake_hydrate(
        agent: str, transcript_id: str | None = None, **_k: Any
    ) -> _FakeBundle:
        hydrate_calls.append(agent)
        return _FakeBundle()

    async def fake_loop(**_k: Any) -> _FakeLoopResult:
        return _FakeLoopResult(provider="openai")

    monkeypatch.setattr(agent_seat, "hydrate_agent", fake_hydrate)
    monkeypatch.setattr(fd_native_mod, "run_native_tool_loop", fake_loop)

    step = _FakeStep(system_prompt="You are a test assistant.")
    context = _make_context(
        options={"model": "openai/gpt-5.4", "mcp": False},
    )

    out = await handler.execute(step, context)

    assert hydrate_calls == []
    signals = [e.signal for e in published_events]
    assert "pipeline.frontier.dispatch.hydrated" not in signals
    assert "pipeline.frontier.dispatch.completed" in signals
    assert out.system_prompt.endswith("You are a test assistant.")
    assert out.json["hydration"] == {"agent": None}


@pytest.mark.asyncio
async def test_handler_missing_model_raises(
    handler: FrontierDispatchHandler,
) -> None:
    """No pipeline_options.model + no model_ref + no model_requirements →
    useful resolution-failure error that names all three valid sources."""
    step = _FakeStep(model_ref=None)
    context = _make_context(options={})
    with pytest.raises(ValueError, match="could not resolve a model") as exc_info:
        await handler.execute(step, context)
    msg = str(exc_info.value)
    assert "pipeline_options.model" in msg
    assert "model_ref" in msg
    assert "model_requirements" in msg


@pytest.mark.asyncio
async def test_handler_resolves_via_model_ref_through_async_delegation(
    handler: FrontierDispatchHandler,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No pipeline_options.model, but step.model_ref → resolved via
    StepConfig.get_target_model_id_async, not a hardwired error."""
    captured: dict[str, Any] = {}

    async def fake_loop(**kwargs: Any) -> _FakeLoopResult:
        captured["model"] = kwargs["model"]
        return _FakeLoopResult(provider="anthropic")

    monkeypatch.setattr(fd_native_mod, "run_native_tool_loop", fake_loop)

    step = _FakeStep(model_ref="anthropic/claude-sonnet-4-6")
    context = _make_context(options={})
    out = await handler.execute(step, context)

    assert captured["model"] == "anthropic/claude-sonnet-4-6"
    assert out.json["provider"] == "anthropic"


@pytest.mark.asyncio
async def test_handler_resolves_via_model_requirements_when_model_ref_absent(
    handler: FrontierDispatchHandler,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No pipeline_options.model, no model_ref, but model_requirements set →
    delegates to StepConfig.get_target_model_id_async (which calls
    get_ranked_candidates via /v1/models/select). Stubbed to return a
    candidate; handler must use it."""
    captured: dict[str, Any] = {}

    async def fake_loop(**kwargs: Any) -> _FakeLoopResult:
        captured["model"] = kwargs["model"]
        return _FakeLoopResult(provider="openai")

    monkeypatch.setattr(fd_native_mod, "run_native_tool_loop", fake_loop)

    step = _FakeStep(
        model_ref=None,
        model_requirements={"task": "synthesis", "min_score": 0.7},
        resolved_async_model="openai/gpt-5.4",
    )
    context = _make_context(options={})
    out = await handler.execute(step, context)

    assert captured["model"] == "openai/gpt-5.4"
    assert out.json["provider"] == "openai"


@pytest.mark.asyncio
async def test_handler_pipeline_options_model_short_circuits_async_resolution(
    handler: FrontierDispatchHandler,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """pipeline_options.model takes precedence over StepConfig delegation —
    the async resolver must not be called when the caller supplies an
    explicit model. Verifies the short-circuit branch and avoids spurious
    /v1/models/select traffic for explicit dispatches."""
    delegation_calls: list[Any] = []

    async def loud_async(*args: Any, **kwargs: Any) -> str | None:
        delegation_calls.append((args, kwargs))
        return "openai/should-not-be-used"

    async def fake_loop(**kwargs: Any) -> _FakeLoopResult:
        return _FakeLoopResult(provider="anthropic")

    monkeypatch.setattr(fd_native_mod, "run_native_tool_loop", fake_loop)

    step = _FakeStep(model_ref="some/model-ref")
    monkeypatch.setattr(step, "get_target_model_id_async", loud_async)

    context = _make_context(options={"model": "anthropic/claude-sonnet-4-6"})
    await handler.execute(step, context)

    assert delegation_calls == []


@pytest.mark.asyncio
async def test_handler_exhausted_emits_exhausted_signal(
    handler: FrontierDispatchHandler,
    monkeypatch: pytest.MonkeyPatch,
    published_events: list[Any],
) -> None:
    async def fake_loop(**_k: Any) -> _FakeLoopResult:
        return _FakeLoopResult(exhausted=True, turns_used=3, provider="xai")

    monkeypatch.setattr(fd_native_mod, "run_native_tool_loop", fake_loop)

    step = _FakeStep()
    context = _make_context(options={"model": "xai/grok-4-fast-reasoning"})

    out = await handler.execute(step, context)
    signals = [e.signal for e in published_events]
    assert "pipeline.frontier.dispatch.exhausted" in signals
    assert "pipeline.frontier.dispatch.completed" not in signals
    assert out.json["exhausted"] is True


@pytest.mark.asyncio
async def test_handler_exhausted_empty_content_raises(
    handler: FrontierDispatchHandler,
    monkeypatch: pytest.MonkeyPatch,
    published_events: list[Any],
) -> None:
    async def fake_loop(**_k: Any) -> _FakeLoopResult:
        return _FakeLoopResult(
            content="", exhausted=True, turns_used=16, provider="openai"
        )

    monkeypatch.setattr(fd_native_mod, "run_native_tool_loop", fake_loop)

    step = _FakeStep()
    context = _make_context(options={"model": "openai/gpt-5.4"})

    with pytest.raises(FrontierDispatchExhaustedError):
        await handler.execute(step, context)

    signals = [e.signal for e in published_events]
    assert "pipeline.frontier.dispatch.exhausted" in signals
    assert "pipeline.frontier.dispatch.completed" not in signals


def test_tool_event_translation_produces_frontier_signals(
    published_events: list[Any],
) -> None:
    from systems.pipeline.core.handlers.frontier_dispatch_streaming import (
        build_on_tool_event,
    )

    context = _make_context(options={})
    cb = build_on_tool_event(context, agent="gatherer", publish=published_events.append)

    cb(
        "pipeline.frontier.dispatch.tool.called",
        {"tool_name": "cortex", "turn": 1, "elapsed_ms": 12.3, "provider": "anthropic"},
    )
    cb(
        "pipeline.frontier.dispatch.tool.failed",
        {"tool_name": "rag", "turn": 2, "elapsed_ms": 5.0, "provider": "openai"},
    )

    signals = [e.signal for e in published_events]
    assert "pipeline.frontier.dispatch.tool.called" in signals
    assert "pipeline.frontier.dispatch.tool.failed" in signals


def test_validate_requires_correct_step_type() -> None:
    h = FrontierDispatchHandler()
    errs = h.validate(_FakeStep(type="something_else"))
    assert any("frontier_dispatch_v1" in e for e in errs)
    errs2 = h.validate(_FakeStep(type="frontier_dispatch_v1"))
    assert errs2 == []


@pytest.mark.asyncio
async def test_handler_anthropic_allows_remote_mcp_false(
    handler: FrontierDispatchHandler,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Anthropic may opt out of server-side remote_mcp (client-side inject)."""

    async def fake_loop(**_k: Any) -> _FakeLoopResult:
        return _FakeLoopResult(provider="anthropic")

    monkeypatch.setattr(fd_native_mod, "run_native_tool_loop", fake_loop)

    step = _FakeStep()
    context = _make_context(
        options={"model": "anthropic/claude-sonnet-4-6", "remote_mcp": False}
    )
    out = await handler.execute(step, context)
    assert out.json["provider"] == "anthropic"


@pytest.mark.parametrize(
    "model,provider",
    [
        ("openai/gpt-5.4", "openai"),
        ("google/gemini-2.5-pro", "google"),
        ("xai/grok-4-fast-reasoning", "xai"),
    ],
)
@pytest.mark.asyncio
async def test_handler_rejects_non_anthropic_remote_mcp_true(
    handler: FrontierDispatchHandler,
    published_events: list[Any],
    model: str,
    provider: str,
) -> None:
    """remote_mcp=True is anthropic-only — every other provider rejects."""
    from systems.pipeline.core.execution.errors import RemoteMcpUnsupportedError

    step = _FakeStep()
    context = _make_context(options={"model": model, "remote_mcp": True})
    with pytest.raises(RemoteMcpUnsupportedError) as exc_info:
        await handler.execute(step, context)
    err = exc_info.value
    assert err.provider == provider
    assert err.requested is True
    assert err.to_dict()["code"] == "remote_mcp_unsupported"
    assert "anthropic-only" in err.reason or "anthropic" in err.reason
    evt = next(
        e
        for e in published_events
        if e.signal == "pipeline.frontier.dispatch.remotemcp.unsupported"
    )
    assert evt.payload["provider"] == provider
    assert evt.payload["requested"] is True


@pytest.mark.asyncio
async def test_handler_rejects_remote_mcp_true_without_mcp(
    handler: FrontierDispatchHandler,
) -> None:
    """remote_mcp=True requires mcp=True — rejecting the combination avoids
    the nonsensical state of asking for server-side MCP when client-side MCP
    tooling has been turned off entirely."""
    from systems.pipeline.core.execution.errors import RemoteMcpUnsupportedError

    step = _FakeStep()
    context = _make_context(
        options={
            "model": "anthropic/claude-sonnet-4-6",
            "mcp": False,
            "remote_mcp": True,
        }
    )
    with pytest.raises(RemoteMcpUnsupportedError) as exc_info:
        await handler.execute(step, context)
    assert "requires mcp=True" in exc_info.value.reason


@pytest.mark.asyncio
async def test_handler_mcp_false_suppresses_tools(
    handler: FrontierDispatchHandler,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """mcp=False short-circuits tool injection regardless of persona mode."""
    captured: dict[str, Any] = {}

    async def fake_loop(**kwargs: Any) -> _FakeLoopResult:
        captured["tools"] = kwargs["req"].tools
        captured["remote_mcp"] = kwargs["req"].remote_mcp
        return _FakeLoopResult(provider="openai")

    monkeypatch.setattr(fd_native_mod, "run_native_tool_loop", fake_loop)

    step = _FakeStep()
    context = _make_context(options={"model": "openai/gpt-5.4", "mcp": False})
    await handler.execute(step, context)
    assert captured["tools"] is None
    assert captured["remote_mcp"] is False


@pytest.mark.asyncio
async def test_handler_non_anthropic_agent_uses_live_mcp_tools(
    handler: FrontierDispatchHandler,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_hydrate(
        agent: str, transcript_id: str | None = None, **_k: Any
    ) -> _FakeBundle:
        return _FakeBundle()

    def fake_assemble(agent: str, **_k: Any) -> str:
        return f"SYSTEM[{agent}]"

    async def fake_defs() -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "web_search",
                    "description": "Search the web",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ]

    async def fake_loop(**kwargs: Any) -> _FakeLoopResult:
        captured["tools"] = kwargs["req"].tools
        return _FakeLoopResult(provider="openai")

    monkeypatch.setattr(agent_seat, "hydrate_agent", fake_hydrate)
    monkeypatch.setattr(agent_seat, "assemble_system_prompt", fake_assemble)
    monkeypatch.setattr(agent_seat, "get_mcp_tool_definitions", fake_defs)
    monkeypatch.setattr(fd_native_mod, "run_native_tool_loop", fake_loop)

    step = _FakeStep()
    context = _make_context(options={"model": "openai/gpt-5.4", "role": "gatherer"})
    await handler.execute(step, context)

    assert [t["function"]["name"] for t in captured["tools"]] == ["web_search"]


@pytest.mark.asyncio
async def test_handler_persona_free_defaults_use_full_mcp_catalog(
    handler: FrontierDispatchHandler,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Persona-free dispatch (no agent) gets the full live MCP catalog.

    Closes the BOE-19-P-vintage divergence (assertion 7974, 2026-05-01) where
    persona-free frontier HTTP (no agent) exposed only the curated read-only tier
    while ``team_dispatch`` (with agent) exposed the full catalog. The
    dispatch path no longer determines the tool surface.
    """
    captured: dict[str, Any] = {}

    async def fake_defs() -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": name,
                    "parameters": {"type": "object", "properties": {}},
                },
            }
            for name in ["cortex", "rag", "agent_bus", "fs", "observability"]
        ]

    async def fake_loop(**kwargs: Any) -> _FakeLoopResult:
        captured["tools"] = kwargs["req"].tools
        return _FakeLoopResult(provider="openai")

    monkeypatch.setattr(agent_seat, "get_mcp_tool_definitions", fake_defs)
    monkeypatch.setattr(fd_native_mod, "run_native_tool_loop", fake_loop)

    step = _FakeStep()
    context = _make_context(options={"model": "openai/gpt-5.4"})
    await handler.execute(step, context)

    assert [t["function"]["name"] for t in captured["tools"]] == [
        "cortex",
        "rag",
        "agent_bus",
        "fs",
        "observability",
    ]


@pytest.mark.asyncio
async def test_handler_endpoint_supplied_tools_accept_live_mcp_names(
    handler: FrontierDispatchHandler,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_resolve(names: list[str]) -> list[dict[str, Any]]:
        assert names == ["web_search"]
        return [
            {
                "type": "function",
                "function": {
                    "name": "web_search",
                    "description": "Search the web",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ]

    async def fake_loop(**kwargs: Any) -> _FakeLoopResult:
        captured["tools"] = kwargs["req"].tools
        return _FakeLoopResult(provider="openai")

    monkeypatch.setattr(agent_seat, "resolve_tool_definitions", fake_resolve)
    monkeypatch.setattr(fd_native_mod, "run_native_tool_loop", fake_loop)

    step = _FakeStep()
    context = _make_context(
        options={"model": "openai/gpt-5.4", "tools": ["web_search"]}
    )
    await handler.execute(step, context)

    assert [t["function"]["name"] for t in captured["tools"]] == ["web_search"]


@pytest.mark.asyncio
async def test_handler_rejects_raw_agent_plus_endpoint_tools(
    handler: FrontierDispatchHandler,
) -> None:
    step = _FakeStep()
    context = _make_context(
        options={
            "model": "openai/gpt-5.4",
            "role": "gatherer",
            "tools": ["web_search"],
        }
    )

    with pytest.raises(ValueError, match="only supported via POST /api/v1/frontier/dispatch"):
        await handler.execute(step, context)


@pytest.mark.asyncio
async def test_handler_endpoint_agent_plus_tools_preserves_persona_metadata(
    handler: FrontierDispatchHandler,
    monkeypatch: pytest.MonkeyPatch,
    published_events: list[Any],
) -> None:
    captured: dict[str, Any] = {}

    async def fake_resolve(names: list[str]) -> list[dict[str, Any]]:
        assert names == ["web_search"]
        return [
            {
                "type": "function",
                "function": {
                    "name": "web_search",
                    "description": "Search the web",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ]

    async def fake_loop(**kwargs: Any) -> _FakeLoopResult:
        captured["tools"] = kwargs["req"].tools
        captured["system"] = kwargs["req"].system
        return _FakeLoopResult(provider="openai")

    monkeypatch.setattr(agent_seat, "resolve_tool_definitions", fake_resolve)
    monkeypatch.setattr(fd_native_mod, "run_native_tool_loop", fake_loop)

    step = _FakeStep()
    context = _make_context(
        options={
            "model": "openai/gpt-5.4",
            "role": "gatherer",
            "system": "SYSTEM[gatherer]",
            "tools": ["web_search"],
            "_endpoint_request_id": "frontier-req-1",
        }
    )

    out = await handler.execute(step, context)

    signals = [e.signal for e in published_events]
    assert "pipeline.frontier.dispatch.hydrated" not in signals
    assert "pipeline.frontier.dispatch.completed" in signals
    assert out.json["hydration"] == {
        "agent": "gatherer",
        "tool_resolution": "endpoint-supplied",
    }
    assert out.system_prompt.endswith("SYSTEM[gatherer]")
    assert [t["function"]["name"] for t in captured["tools"]] == ["web_search"]


@pytest.mark.asyncio
async def test_handler_rejects_unknown_reasoning_effort(
    handler: FrontierDispatchHandler,
) -> None:
    step = _FakeStep()
    context = _make_context(
        options={
            "model": "openai/gpt-5.4",
            "mcp": False,
            "generation_parameters": {"reasoning_effort": "hi"},
        }
    )

    with pytest.raises(ValueError, match="reasoning_effort='hi'"):
        await handler.execute(step, context)


@pytest.mark.asyncio
@pytest.mark.parametrize("effort", ["low", "medium", "high"])
async def test_handler_anthropic_opus47_reasoning_effort_uses_adaptive_thinking(
    handler: FrontierDispatchHandler,
    monkeypatch: pytest.MonkeyPatch,
    effort: str,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_loop(**kwargs: Any) -> _FakeLoopResult:
        captured["req"] = kwargs["req"]
        return _FakeLoopResult(provider="anthropic")

    monkeypatch.setattr(fd_native_mod, "run_native_tool_loop", fake_loop)

    step = _FakeStep()
    context = _make_context(
        options={
            "model": "anthropic/claude-opus-4-7",
            "mcp": False,
            "generation_parameters": {"reasoning_effort": effort},
        }
    )

    await handler.execute(step, context)

    req = captured["req"]
    assert req.thinking == {"type": "adaptive"}
    assert req.effort == effort


@pytest.mark.asyncio
async def test_handler_anthropic_legacy_reasoning_effort_keeps_budget_tokens(
    handler: FrontierDispatchHandler,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_loop(**kwargs: Any) -> _FakeLoopResult:
        captured["req"] = kwargs["req"]
        return _FakeLoopResult(provider="anthropic")

    monkeypatch.setattr(fd_native_mod, "run_native_tool_loop", fake_loop)

    step = _FakeStep()
    context = _make_context(
        options={
            "model": "anthropic/claude-opus-4-5",
            "mcp": False,
            "generation_parameters": {"reasoning_effort": "medium"},
        }
    )

    await handler.execute(step, context)

    assert captured["req"].thinking == {"type": "enabled", "budget_tokens": 8192}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "model",
    [
        "anthropic/claude-opus-4-7",
        "anthropic/claude-opus-4-6",
        "anthropic/claude-sonnet-4-6",
        "anthropic/claude-mythos-preview",
    ],
)
async def test_handler_anthropic_adaptive_family_uses_adaptive_thinking(
    handler: FrontierDispatchHandler,
    monkeypatch: pytest.MonkeyPatch,
    model: str,
) -> None:
    """Adaptive thinking applies to the documented Anthropic adaptive set:
    Mythos Preview, Opus 4.7, Opus 4.6, Sonnet 4.6 (per
    docs/thirdparty/claude-api/upstream/adaptive-thinking.md). Effort flows
    via req.effort → output_config.effort for all four."""
    captured: dict[str, Any] = {}

    async def fake_loop(**kwargs: Any) -> _FakeLoopResult:
        captured["req"] = kwargs["req"]
        return _FakeLoopResult(provider="anthropic")

    monkeypatch.setattr(fd_native_mod, "run_native_tool_loop", fake_loop)

    step = _FakeStep()
    context = _make_context(
        options={
            "model": model,
            "mcp": False,
            "generation_parameters": {"reasoning_effort": "high"},
        }
    )

    await handler.execute(step, context)

    req = captured["req"]
    assert req.thinking == {"type": "adaptive"}
    assert req.effort == "high"


@pytest.mark.asyncio
@pytest.mark.parametrize("effort", ["none", "minimal", "xhigh", "max"])
async def test_handler_extended_reasoning_effort_vocabulary_accepted(
    handler: FrontierDispatchHandler,
    monkeypatch: pytest.MonkeyPatch,
    effort: str,
) -> None:
    """Extended vocabulary (none/minimal/xhigh/max) is admitted per the
    documented provider effort surfaces (OpenAI, Anthropic adaptive,
    Gemini 3). On Opus 4.7 (adaptive-capable) the value flows to
    req.effort → output_config.effort; the handler must not ValueError."""
    captured: dict[str, Any] = {}

    async def fake_loop(**kwargs: Any) -> _FakeLoopResult:
        captured["req"] = kwargs["req"]
        return _FakeLoopResult(provider="anthropic")

    monkeypatch.setattr(fd_native_mod, "run_native_tool_loop", fake_loop)

    step = _FakeStep()
    context = _make_context(
        options={
            "model": "anthropic/claude-opus-4-7",
            "mcp": False,
            "generation_parameters": {"reasoning_effort": effort},
        }
    )

    await handler.execute(step, context)

    req = captured["req"]
    assert req.thinking == {"type": "adaptive"}
    assert req.effort == effort


@pytest.mark.asyncio
@pytest.mark.parametrize("effort", ["none", "minimal", "xhigh", "max"])
async def test_handler_anthropic_legacy_skips_thinking_for_extended_effort(
    handler: FrontierDispatchHandler,
    monkeypatch: pytest.MonkeyPatch,
    effort: str,
) -> None:
    """Legacy budget-mode Anthropic has no documented mapping for extended
    effort values; the handler must skip the thinking config rather than
    fake a budget. The raw effort still rides on req.effort."""
    captured: dict[str, Any] = {}

    async def fake_loop(**kwargs: Any) -> _FakeLoopResult:
        captured["req"] = kwargs["req"]
        return _FakeLoopResult(provider="anthropic")

    monkeypatch.setattr(fd_native_mod, "run_native_tool_loop", fake_loop)

    step = _FakeStep()
    context = _make_context(
        options={
            "model": "anthropic/claude-opus-4-5",
            "mcp": False,
            "generation_parameters": {"reasoning_effort": effort},
        }
    )

    await handler.execute(step, context)

    req = captured["req"]
    assert req.thinking is None
    assert req.effort == effort


@pytest.mark.asyncio
async def test_handler_google_effort_is_lowercase(
    handler: FrontierDispatchHandler,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Google receives effort as lowercase. The adapter parses
    thinkingLevel (Gemini 3) or thinkingBudget (Gemini 2.5) from this; the
    Gemini docs document lowercase enum values."""
    captured: dict[str, Any] = {}

    async def fake_loop(**kwargs: Any) -> _FakeLoopResult:
        captured["req"] = kwargs["req"]
        return _FakeLoopResult(provider="google")

    monkeypatch.setattr(fd_native_mod, "run_native_tool_loop", fake_loop)

    step = _FakeStep()
    context = _make_context(
        options={
            "model": "google/gemini-3.5-flash",
            "mcp": False,
            "generation_parameters": {"reasoning_effort": "low"},
        }
    )

    await handler.execute(step, context)

    assert captured["req"].thinking == {"effort": "low"}


@pytest.mark.asyncio
async def test_handler_grok43_defaults_to_high_reasoning_effort(
    handler: FrontierDispatchHandler,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``xai/grok-4.3`` with no caller-supplied ``reasoning_effort`` must
    default to ``high`` — measured plan-quality gap closes at high effort
    (cortex thread 1024, 2026-05-17). Applies across every dispatch surface
    that routes through ``frontier_dispatch_v1``.
    """
    captured: dict[str, Any] = {}

    async def fake_loop(**kwargs: Any) -> _FakeLoopResult:
        captured["req"] = kwargs["req"]
        return _FakeLoopResult(provider="xai")

    monkeypatch.setattr(fd_native_mod, "run_native_tool_loop", fake_loop)

    step = _FakeStep()
    context = _make_context(options={"model": "xai/grok-4.3", "mcp": False})

    await handler.execute(step, context)

    assert captured["req"].effort == "high"
    # Provider-native ``effort`` shape for xAI is ``{"effort": "high"}``.
    assert captured["req"].thinking == {"effort": "high"}


@pytest.mark.asyncio
async def test_handler_grok43_caller_effort_wins_over_default(
    handler: FrontierDispatchHandler,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Explicit caller ``reasoning_effort`` always wins over the model default.

    Caller passes ``medium``; default is ``high``; the dispatched request
    must carry the caller value.
    """
    captured: dict[str, Any] = {}

    async def fake_loop(**kwargs: Any) -> _FakeLoopResult:
        captured["req"] = kwargs["req"]
        return _FakeLoopResult(provider="xai")

    monkeypatch.setattr(fd_native_mod, "run_native_tool_loop", fake_loop)

    step = _FakeStep()
    context = _make_context(
        options={
            "model": "xai/grok-4.3",
            "mcp": False,
            "generation_parameters": {"reasoning_effort": "medium"},
        }
    )

    await handler.execute(step, context)

    assert captured["req"].effort == "medium"


@pytest.mark.asyncio
async def test_handler_grok43_empty_string_effort_triggers_default(
    handler: FrontierDispatchHandler,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty-string ``reasoning_effort`` (the MCP wrapper convention for
    "unset") must trigger the model default. The MCP tool surface passes
    ``reasoning_effort or ""``, so the default-resolution gate has to treat
    empty strings as unset to fire at all from that path.
    """
    captured: dict[str, Any] = {}

    async def fake_loop(**kwargs: Any) -> _FakeLoopResult:
        captured["req"] = kwargs["req"]
        return _FakeLoopResult(provider="xai")

    monkeypatch.setattr(fd_native_mod, "run_native_tool_loop", fake_loop)

    step = _FakeStep()
    context = _make_context(
        options={
            "model": "xai/grok-4.3",
            "mcp": False,
            "generation_parameters": {"reasoning_effort": ""},
        }
    )

    await handler.execute(step, context)

    assert captured["req"].effort == "high"


@pytest.mark.asyncio
async def test_handler_non_default_model_unaffected_by_default_resolution(
    handler: FrontierDispatchHandler,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Models with no registry default-high-effort entry are not coerced. With
    no caller effort, ``capability_dispatch.default_reasoning_effort`` returns
    None, so ``req.effort`` is None and ``req.thinking`` is None — the
    provider-native default takes over.
    """
    captured: dict[str, Any] = {}

    async def fake_loop(**kwargs: Any) -> _FakeLoopResult:
        captured["req"] = kwargs["req"]
        return _FakeLoopResult(provider="openai")

    monkeypatch.setattr(fd_native_mod, "run_native_tool_loop", fake_loop)

    step = _FakeStep()
    context = _make_context(options={"model": "openai/gpt-5.4", "mcp": False})

    await handler.execute(step, context)

    assert captured["req"].effort is None
    assert captured["req"].thinking is None


def test_resolve_default_reasoning_effort_grok43_returns_high() -> None:
    """Unit test: ``xai/grok-4.3`` resolves to ``high``."""
    from .frontier_dispatch_request import resolve_default_reasoning_effort

    assert resolve_default_reasoning_effort("xai/grok-4.3") == "high"


@pytest.mark.parametrize(
    "model",
    [
        "openai/gpt-5.4",
        "openai/gpt-5.5",
        "anthropic/claude-sonnet-4-6",
        "xai/grok-4-fast-reasoning",
        "google/gemini-2.5-pro",
        None,
        "",
    ],
)
def test_resolve_default_reasoning_effort_other_models_return_none(
    model: str | None,
) -> None:
    """Unit test: every non-listed model (and falsy values) returns None."""
    from .frontier_dispatch_request import resolve_default_reasoning_effort

    assert resolve_default_reasoning_effort(model) is None


@pytest.mark.asyncio
async def test_handler_surfaces_canonical_model_entity_id(
    handler: FrontierDispatchHandler,
    monkeypatch: pytest.MonkeyPatch,
    published_events: list[Any],
) -> None:
    async def fake_loop(**_kwargs: Any) -> _FakeLoopResult:
        return _FakeLoopResult(provider="google")

    monkeypatch.setattr(fd_native_mod, "run_native_tool_loop", fake_loop)

    step = _FakeStep()
    context = _make_context(
        options={
            "model": "google/gemini-3.1-pro-preview",
            "mcp": False,
        }
    )

    out = await handler.execute(step, context)

    assert out.json["model_entity_id"] == "model:gemini-3.1-pro-preview"
    started = next(
        e for e in published_events if e.signal == "pipeline.frontier.dispatch.started"
    )
    assert started.payload["model"] == "google/gemini-3.1-pro-preview"
    assert started.payload["model_entity_id"] == "model:gemini-3.1-pro-preview"


def test_resolve_remote_mcp_defaults_by_provider() -> None:
    from systems.pipeline.core.handlers.frontier_dispatch_admission import (
        resolve_remote_mcp,
    )

    step = _FakeStep()
    published: list[Any] = []
    # Default: True iff provider=anthropic AND mcp_enabled.
    cases = [
        ("anthropic/claude-sonnet-4-6", "anthropic", True, True),
        ("anthropic/claude-sonnet-4-6", "anthropic", False, False),
        ("openai/gpt-5.4", "openai", True, False),
        ("google/gemini-2.5-pro", "google", True, False),
        ("xai/grok-4-fast-reasoning", "xai", True, False),
    ]
    for model, provider, mcp_enabled, expected in cases:
        context = _make_context(options={"model": model})
        result = resolve_remote_mcp(
            opts=context.options,
            step=step,
            context=context,
            provider=provider,
            model=model,
            model_entity_id=_TEST_MODEL_ENTITY_ID,
            agent=None,
            mcp_enabled=mcp_enabled,
            publish=published.append,
        )
        assert result is expected, (
            f"{provider} mcp={mcp_enabled}: expected {expected}, got {result}"
        )


def test_resolve_agent_uses_options_then_domain_field() -> None:
    """Phase 5: resolve_agent reads pipeline_options.role > step.role > None.

    The function name retains its historical 'agent' suffix (limit blast
    radius across module-internal references); the *return value* is a
    normalized role slug used for Cortex role:{slug} resolution.
    """
    from .frontier_dispatch_request import resolve_agent

    step_with_domain = _FakeStep(domain_fields={"role": "synthesizer"})
    assert resolve_agent({}, step_with_domain) == "synthesizer"
    assert resolve_agent({"role": "web"}, step_with_domain) == "claude-web"
    assert resolve_agent({"role": ""}, step_with_domain) == "synthesizer"
    assert resolve_agent({}, _FakeStep()) is None


# ---------------------------------------------------------------------------
# Admission guard tests
# ---------------------------------------------------------------------------


def test_reject_unknown_runtime_options_raises_on_unknown_keys(
    handler: FrontierDispatchHandler,
) -> None:
    """Unknown ``runtime_options`` keys must raise ``UnknownPipelineOptionsError``."""
    from systems.pipeline.core.execution.errors import UnknownPipelineOptionsError
    from systems.pipeline.core.handlers.frontier_dispatch_admission import (
        reject_unknown_runtime_options,
    )

    step = _FakeStep()
    context = _make_context(
        options={"model": "anthropic/claude-sonnet-4-6"},
        runtime_options={"unknown_key": True, "another_bad_key": 42},
    )
    with pytest.raises(UnknownPipelineOptionsError) as exc_info:
        reject_unknown_runtime_options(
            step, context, handler._ACCEPTED_RUNTIME_OPTION_KEYS
        )
    assert "unknown_key" in str(exc_info.value)


def test_reject_unknown_runtime_options_passes_on_accepted_keys(
    handler: FrontierDispatchHandler,
) -> None:
    """All keys in ``_ACCEPTED_RUNTIME_OPTION_KEYS`` must be admitted without error."""
    from systems.pipeline.core.handlers.frontier_dispatch_admission import (
        reject_unknown_runtime_options,
    )

    step = _FakeStep()
    context = _make_context(
        options={"model": "anthropic/claude-sonnet-4-6"},
        runtime_options={
            k: True for k in FrontierDispatchHandler._ACCEPTED_RUNTIME_OPTION_KEYS
        },
    )
    reject_unknown_runtime_options(step, context, handler._ACCEPTED_RUNTIME_OPTION_KEYS)


def test_reject_unknown_runtime_options_ignores_injected_stream_flag(
    handler: FrontierDispatchHandler,
) -> None:
    """Framework-injected ``stream`` must NOT trip the unknown-key gate.

    Regression for the all-generate-fails outage: ``_coerce_stream_flag`` at
    proxy ingress injects ``stream`` into the request body, which
    ``extract_runtime_options`` folds into ``runtime_options`` for the generate
    handler's passthrough branch. Frontier dispatch is non-streaming and does
    not list ``stream`` in ``_ACCEPTED_RUNTIME_OPTION_KEYS``; the gate must
    treat it as framework-injected and admit the dispatch.
    """
    from systems.pipeline.core.handlers.frontier_dispatch_admission import (
        reject_unknown_runtime_options,
    )

    step = _FakeStep()
    context = _make_context(
        options={"model": "anthropic/claude-sonnet-4-6"},
        runtime_options={"stream": False, "model": "anthropic/claude-sonnet-4-6"},
    )
    reject_unknown_runtime_options(step, context, handler._ACCEPTED_RUNTIME_OPTION_KEYS)


def test_check_agent_model_consistency_allows_role_provider_override() -> None:
    """Functional roles are model-agnostic; explicit models may cross providers."""
    from systems.pipeline.core.handlers.frontier_dispatch_admission import (
        check_agent_model_consistency,
    )

    published: list[Any] = []

    check_agent_model_consistency(
        agent="reviewer",
        model="openai/gpt-5.5",
        model_entity_id=_TEST_MODEL_ENTITY_ID,
        provider="openai",
        execution_id="exec-test-0001",
        publish=published.append,
    )

    assert published == []


def test_check_agent_model_consistency_rejects_concrete_seat_mismatch() -> None:
    """Concrete family/platform seats remain provider-bound."""
    from systems.pipeline.core.execution.errors import AgentModelMismatchError
    from systems.pipeline.core.handlers.frontier_dispatch_admission import (
        check_agent_model_consistency,
    )

    published: list[Any] = []

    with pytest.raises(AgentModelMismatchError) as exc_info:
        check_agent_model_consistency(
            agent="grok-api-multi",
            model="anthropic/claude-sonnet-4-6",
            model_entity_id=_TEST_MODEL_ENTITY_ID,
            provider="anthropic",
            execution_id="exec-test-0001",
            publish=published.append,
        )

    err = exc_info.value
    assert err.agent == "grok-api-multi"
    assert err.provider == "anthropic"
    assert err.expected_provider == "xai"
    assert err.required_variant is None
    assert err.to_dict()["code"] == "agent_model_mismatch"
    assert len(published) == 1
    assert published[0].signal == "pipeline.frontier.dispatch.mismatch"
    assert published[0].payload["agent"] == "grok-api-multi"
    assert published[0].payload["requested_model"] == "anthropic/claude-sonnet-4-6"
    assert published[0].payload["mismatch_kind"] == "provider"


def test_check_agent_model_consistency_accepts_valid_family() -> None:
    """Matching agent/provider with a multi-agent model must not raise or emit."""
    from systems.pipeline.core.handlers.frontier_dispatch_admission import (
        check_agent_model_consistency,
    )

    published: list[Any] = []

    check_agent_model_consistency(
        agent="skeptic",
        model="xai/grok-4.20-multi-agent-0309",
        model_entity_id=_TEST_MODEL_ENTITY_ID,
        provider="xai",
        execution_id="exec-test-0001",
        publish=published.append,
    )

    assert published == []


def test_check_agent_model_consistency_allows_non_multi_agent_for_skeptic_role() -> (
    None
):
    """Skeptic is a functional role; multi-agent is only the default seat."""
    from systems.pipeline.core.handlers.frontier_dispatch_admission import (
        check_agent_model_consistency,
    )

    published: list[Any] = []

    check_agent_model_consistency(
        agent="skeptic",
        model="anthropic/claude-sonnet-4-6",
        model_entity_id=_TEST_MODEL_ENTITY_ID,
        provider="anthropic",
        execution_id="exec-test-0002",
        publish=published.append,
    )

    assert published == []


def test_check_agent_model_consistency_rejects_non_multi_agent_for_concrete_seat() -> (
    None
):
    """grok-api-multi still requires a multi-agent xAI model."""
    from systems.pipeline.core.execution.errors import AgentModelMismatchError
    from systems.pipeline.core.handlers.frontier_dispatch_admission import (
        check_agent_model_consistency,
    )

    published: list[Any] = []

    with pytest.raises(AgentModelMismatchError) as exc_info:
        check_agent_model_consistency(
            agent="grok-api-multi",
            model="xai/grok-4-fast-reasoning",
            model_entity_id=_TEST_MODEL_ENTITY_ID,
            provider="xai",
            execution_id="exec-test-0002",
            publish=published.append,
        )

    err = exc_info.value
    assert err.agent == "grok-api-multi"
    assert err.model == "xai/grok-4-fast-reasoning"
    assert err.expected_provider == "xai"
    assert err.required_variant == "multi-agent"
    assert err.to_dict()["code"] == "agent_model_mismatch"
    assert len(published) == 1
    assert published[0].signal == "pipeline.frontier.dispatch.mismatch"
    assert published[0].payload["mismatch_kind"] == "variant"


def test_check_agent_model_consistency_passes_unknown_agent() -> None:
    """Unknown agents (not in registry) are not checked — custom slugs are allowed."""
    from systems.pipeline.core.handlers.frontier_dispatch_admission import (
        check_agent_model_consistency,
    )

    published: list[Any] = []

    check_agent_model_consistency(
        agent="custom-bot",
        model="anthropic/claude-sonnet-4-6",
        model_entity_id=_TEST_MODEL_ENTITY_ID,
        provider="anthropic",
        execution_id="exec-test-0001",
        publish=published.append,
    )

    assert published == []


@pytest.mark.parametrize(
    "agent,model,provider",
    [
        ("gatherer", "openai/gpt-5.4", "openai"),
        ("synthesizer", "google/gemini-2.5-pro", "google"),
        ("reviewer", "openai/gpt-5.5", "openai"),
    ],
)
def test_check_agent_model_consistency_accepts_agents_without_variant_requirement(
    agent: str,
    model: str,
    provider: str,
) -> None:
    """Known agents without _AGENT_MODEL_REQUIREMENTS entries pass without event."""
    from systems.pipeline.core.handlers.frontier_dispatch_admission import (
        check_agent_model_consistency,
    )

    published: list[Any] = []

    check_agent_model_consistency(
        agent=agent,
        model=model,
        model_entity_id=_TEST_MODEL_ENTITY_ID,
        provider=provider,
        execution_id="exec-test-s4",
        publish=published.append,
    )

    assert published == []


@pytest.mark.parametrize("agent", ["skeptic"])
def test_registry_default_model_satisfies_own_requirement(agent: str) -> None:
    """Default model for skeptic must satisfy the 'multi-agent' variant requirement.

    Regression guard: skeptic default must satisfy the multi-agent variant
    requirement on grok-api-multi.
    """
    from agent_seat.registry import check_agent_model_requirement, resolve_agent_model

    default = resolve_agent_model(agent)
    violation = check_agent_model_requirement(agent, default)
    assert violation is None, (
        f"default model for {agent!r} ({default!r}) violates its own "
        f"requirement: {violation}"
    )


@pytest.mark.asyncio
async def test_handler_persona_free_accepts_multi_agent_model(
    handler: FrontierDispatchHandler,
    monkeypatch: pytest.MonkeyPatch,
    published_events: list[Any],
) -> None:
    """agent=None + multi-agent model is accepted — the invariant is one-way.

    skeptic binds to multi-agent; multi-agent does not bind the caller to skeptic.
    Locks the asymmetry: persona-free dispatches with xai/grok-4.20-multi-agent-0309
    must not trigger the admission gate.
    """

    async def fake_loop(**_k: Any) -> _FakeLoopResult:
        return _FakeLoopResult(provider="xai")

    monkeypatch.setattr(fd_native_mod, "run_native_tool_loop", fake_loop)

    step = _FakeStep()
    context = _make_context(
        options={"model": "xai/grok-4.20-multi-agent-0309", "mcp": False},
    )
    out = await handler.execute(step, context)

    signals = [e.signal for e in published_events]
    assert "pipeline.frontier.dispatch.mismatch" not in signals
    assert "pipeline.frontier.dispatch.completed" in signals
    assert out.json["hydration"] == {"agent": None}


# ---------------------------------------------------------------------------
# XAI server-side built-in tool injection (Skeptic / xAI multi-agent)
# ---------------------------------------------------------------------------


def _make_skeptic_context(**extra_opts: Any) -> SimpleNamespace:
    return _make_context(
        options={
            "model": "xai/grok-4.20-multi-agent-0309",
            "role": "skeptic",
            **extra_opts,
        }
    )


def _make_skeptic_fixtures(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Patch hydrate_agent / assemble_system_prompt / run_native_tool_loop."""
    captured: dict[str, Any] = {}

    async def fake_hydrate(
        agent: str, transcript_id: str | None = None, **_k: Any
    ) -> _FakeBundle:
        return _FakeBundle()

    def fake_assemble(agent: str, **_k: Any) -> str:
        return f"SYSTEM[{agent}]"

    async def fake_loop(**kwargs: Any) -> _FakeLoopResult:
        captured["req"] = kwargs["req"]
        return _FakeLoopResult(provider="xai")

    monkeypatch.setattr(agent_seat, "hydrate_agent", fake_hydrate)
    monkeypatch.setattr(agent_seat, "assemble_system_prompt", fake_assemble)
    monkeypatch.setattr(fd_native_mod, "run_native_tool_loop", fake_loop)
    return captured


@pytest.mark.asyncio
async def test_skeptic_injects_xai_builtin_tools_by_default(
    handler: FrontierDispatchHandler,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Skeptic gets all three built-in tools when options are omitted."""
    captured = _make_skeptic_fixtures(monkeypatch)

    await handler.execute(_FakeStep(), _make_skeptic_context())

    po = captured["req"].provider_options
    assert po is not None
    assert po.get("xai", {}).get("tools") == XAI_BUILTIN_TOOLS
    assert captured["req"].tools is None  # no client-side tools


@pytest.mark.asyncio
async def test_skeptic_caller_provider_options_tools_overrides_default(
    handler: FrontierDispatchHandler,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Caller-supplied provider_options.xai.tools takes precedence over the default."""
    captured = _make_skeptic_fixtures(monkeypatch)

    context = _make_skeptic_context(
        generation_parameters={
            "provider_options": {"xai": {"tools": [{"type": "x_search"}]}}
        }
    )
    await handler.execute(_FakeStep(), context)

    po = captured["req"].provider_options
    assert po["xai"]["tools"] == [{"type": "x_search"}]


@pytest.mark.asyncio
async def test_skeptic_caller_empty_provider_options_tools_suppresses_injection(
    handler: FrontierDispatchHandler,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Caller provider_options.xai.tools=[] suppresses all server-side tools."""
    captured = _make_skeptic_fixtures(monkeypatch)

    context = _make_skeptic_context(
        generation_parameters={"provider_options": {"xai": {"tools": []}}}
    )
    await handler.execute(_FakeStep(), context)

    po = captured["req"].provider_options
    assert po["xai"]["tools"] == []


@pytest.mark.asyncio
async def test_skeptic_mcp_false_suppresses_xai_builtin_injection(
    handler: FrontierDispatchHandler,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """mcp=False is the unified 'no tools' signal — suppresses xAI injection."""
    captured = _make_skeptic_fixtures(monkeypatch)

    await handler.execute(_FakeStep(), _make_skeptic_context(mcp=False))

    po = captured["req"].provider_options
    # provider_options may be None or lack xai.tools — injection must NOT have fired
    if po is not None:
        assert "tools" not in po.get("xai", {})


@pytest.mark.asyncio
async def test_skeptic_explicit_tools_via_frontier_dispatch_suppresses_injection(
    handler: FrontierDispatchHandler,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Explicit pipeline_options.tools via /api/v1/frontier/dispatch bypasses injection.

    When a caller passes tools=[] to force persona-only mode (via
    _endpoint_request_id), the xAI server-side built-in injection must not fire.
    """
    captured: dict[str, Any] = {}

    async def fake_resolve(names: list[str]) -> list[dict[str, Any]]:
        return []

    async def fake_loop(**kwargs: Any) -> _FakeLoopResult:
        captured["req"] = kwargs["req"]
        return _FakeLoopResult(provider="xai")

    monkeypatch.setattr(agent_seat, "resolve_tool_definitions", fake_resolve)
    monkeypatch.setattr(fd_native_mod, "run_native_tool_loop", fake_loop)

    context = _make_skeptic_context(
        tools=[],
        _endpoint_request_id="frontier-req-skeptic-1",
    )
    await handler.execute(_FakeStep(), context)

    po = captured["req"].provider_options
    if po is not None:
        assert "tools" not in po.get("xai", {})


# ---------------------------------------------------------------------------
# Agent-tier suppression: capability_tier=inline-only forces tools=[] regardless
# of provider/model. Orthogonal to the xai-multi-agent suppression.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inline_only_capability_tier_forces_empty_tool_surface(
    handler: FrontierDispatchHandler,
    monkeypatch: pytest.MonkeyPatch,
    published_events: list[Any],
) -> None:
    """Artisan runs ``xai/grok-4.20-0309-reasoning`` (NOT a multi-agent model),
    so the existing provider-derived suppression does not catch it. With
    ``capability_tier=inline-only`` set on the entity, the agent-tier check
    must coerce ``tools=[]`` and emit ``tool.suppressed`` with reason
    ``capability_tier_inline_only``.
    """
    captured: dict[str, Any] = {}

    async def fake_hydrate(
        agent: str, transcript_id: str | None = None, **_k: Any
    ) -> _FakeBundle:
        return _FakeBundle(capability_tier="inline-only")

    def fake_assemble(agent: str, **kwargs: Any) -> str:
        captured["include_quickref"] = kwargs.get("include_cortex_quickref")
        return f"SYSTEM[{agent}]"

    async def fake_loop(**kwargs: Any) -> _FakeLoopResult:
        captured["req"] = kwargs["req"]
        return _FakeLoopResult(provider="xai")

    monkeypatch.setattr(agent_seat, "hydrate_agent", fake_hydrate)
    monkeypatch.setattr(agent_seat, "assemble_system_prompt", fake_assemble)
    monkeypatch.setattr(fd_native_mod, "run_native_tool_loop", fake_loop)

    context = _make_context(
        options={"model": "xai/grok-4.20-0309-reasoning", "role": "artisan"},
    )
    await handler.execute(_FakeStep(), context)

    suppressed = [
        e
        for e in published_events
        if e.signal == "pipeline.frontier.dispatch.tool.suppressed"
    ]
    assert len(suppressed) == 1
    assert suppressed[0].payload["reason"] == "capability_tier_inline_only"
    assert suppressed[0].payload["agent"] == "artisan"
    assert captured["req"].tools is None  # tools=[] flows downstream as None
    assert captured["include_quickref"] is False


@pytest.mark.asyncio
async def test_default_capability_tier_does_not_suppress(
    handler: FrontierDispatchHandler,
    monkeypatch: pytest.MonkeyPatch,
    published_events: list[Any],
) -> None:
    """Without ``capability_tier=inline-only`` on the entity, the agent-tier
    gate is a no-op — provider-derived paths still apply normally.
    """
    captured: dict[str, Any] = {}

    async def fake_hydrate(
        agent: str, transcript_id: str | None = None, **_k: Any
    ) -> _FakeBundle:
        return _FakeBundle(capability_tier=None)

    async def fake_resolve(
        names: tuple[str, ...], *, fallback: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {"name": "fs", "description": "", "parameters": {}},
            }
        ]

    def fake_assemble(agent: str, **kwargs: Any) -> str:
        captured["include_quickref"] = kwargs.get("include_cortex_quickref")
        return f"SYSTEM[{agent}]"

    async def fake_loop(**_k: Any) -> _FakeLoopResult:
        return _FakeLoopResult(provider="anthropic")

    from systems.pipeline.core.handlers import frontier_dispatch_tools as fdt_mod

    monkeypatch.setattr(agent_seat, "hydrate_agent", fake_hydrate)
    monkeypatch.setattr(fdt_mod, "resolve_default_tools", fake_resolve)
    monkeypatch.setattr(agent_seat, "assemble_system_prompt", fake_assemble)
    monkeypatch.setattr(fd_native_mod, "run_native_tool_loop", fake_loop)

    # ``remote_mcp: False`` forces the client-side tool branch (default for
    # anthropic is remote_mcp=True, which would empty the tool set first).
    context = _make_context(
        options={
            "model": "anthropic/claude-sonnet-4-6",
            "role": "reviewer",
            "remote_mcp": False,
        },
    )
    await handler.execute(_FakeStep(), context)

    suppressed = [
        e
        for e in published_events
        if e.signal == "pipeline.frontier.dispatch.tool.suppressed"
    ]
    assert suppressed == []
    assert captured["include_quickref"] is True
