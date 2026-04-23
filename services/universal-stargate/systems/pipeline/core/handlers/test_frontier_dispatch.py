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

import pytest

from systems.pipeline.core.handlers import frontier_dispatch as fd_mod
from systems.pipeline.core.handlers.frontier_dispatch import (
    FrontierDispatchHandler,
)


class _FakeStep:
    """Minimal StepConfig surface used by the handler."""

    def __init__(
        self,
        *,
        name: str = "respond",
        type: str = "frontier_dispatch_v1",
        model_ref: str | None = None,
        system_prompt: str | None = None,
        generation_parameters: dict[str, Any] | None = None,
        domain_fields: dict[str, Any] | None = None,
    ) -> None:
        self.name = name
        self.type = type
        self.model_ref = model_ref
        self.system_prompt = system_prompt
        self.generation_parameters = generation_parameters or {}
        self.handler_inputs: dict[str, Any] = {}
        self._domain = domain_fields or {}

    @property
    def id(self) -> str:
        return self.name

    def get_domain_field(self, key: str, default: Any = None) -> Any:
        return self._domain.get(key, default)


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
        _proxy=SimpleNamespace(pipeline_dispatch_tracker=None, event_bus=None),
    )


class _FakeBundle:
    def __init__(self) -> None:
        self.briefing_card_md = "# Briefing"
        self.continuation_md = None
        self.section_counts = {"briefing_bytes": 42, "todos": 3}
        self.continuation_id = None


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
    async def fake_hydrate(agent: str, transcript_id: str | None) -> _FakeBundle:
        return _FakeBundle()

    def fake_assemble(agent: str, **_k: Any) -> str:
        return f"SYSTEM[{agent}]"

    async def fake_loop(**_k: Any) -> _FakeLoopResult:
        return _FakeLoopResult(provider="xai")

    monkeypatch.setattr(fd_mod, "hydrate_agent", fake_hydrate)
    monkeypatch.setattr(fd_mod, "assemble_system_prompt", fake_assemble)
    monkeypatch.setattr(fd_mod, "run_native_tool_loop", fake_loop)

    step = _FakeStep()
    context = _make_context(
        options={"model": "xai/grok-4-fast-reasoning", "agent": "oppie"},
    )

    out = await handler.execute(step, context)

    signals = [e.signal for e in published_events]
    assert "pipeline.frontier.dispatch.hydrated" in signals
    assert "pipeline.frontier.dispatch.completed" in signals
    assert out.system_prompt.endswith("SYSTEM[oppie]")
    assert out.json["hydration"]["agent"] == "oppie"
    assert out.json["provider"] == "xai"


@pytest.mark.asyncio
async def test_handler_persona_free_mode_skips_hydration(
    handler: FrontierDispatchHandler,
    monkeypatch: pytest.MonkeyPatch,
    published_events: list[Any],
) -> None:
    hydrate_calls: list[str] = []

    async def fake_hydrate(agent: str, transcript_id: str | None) -> _FakeBundle:
        hydrate_calls.append(agent)
        return _FakeBundle()

    async def fake_loop(**_k: Any) -> _FakeLoopResult:
        return _FakeLoopResult(provider="openai")

    monkeypatch.setattr(fd_mod, "hydrate_agent", fake_hydrate)
    monkeypatch.setattr(fd_mod, "run_native_tool_loop", fake_loop)

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
    step = _FakeStep(model_ref=None)
    context = _make_context(options={})
    with pytest.raises(ValueError, match="requires pipeline_options.model"):
        await handler.execute(step, context)


@pytest.mark.asyncio
async def test_handler_exhausted_emits_exhausted_signal(
    handler: FrontierDispatchHandler,
    monkeypatch: pytest.MonkeyPatch,
    published_events: list[Any],
) -> None:
    async def fake_loop(**_k: Any) -> _FakeLoopResult:
        return _FakeLoopResult(exhausted=True, turns_used=3, provider="xai")

    monkeypatch.setattr(fd_mod, "run_native_tool_loop", fake_loop)

    step = _FakeStep()
    context = _make_context(options={"model": "xai/grok-4-fast-reasoning"})

    out = await handler.execute(step, context)
    signals = [e.signal for e in published_events]
    assert "pipeline.frontier.dispatch.exhausted" in signals
    assert "pipeline.frontier.dispatch.completed" not in signals
    assert out.json["exhausted"] is True


def test_tool_event_translation_produces_frontier_signals(
    handler: FrontierDispatchHandler,
    published_events: list[Any],
) -> None:
    context = _make_context(options={})
    cb = handler._build_on_tool_event(context, agent="orion")

    cb(
        "pipeline.frontier.dispatch.tool.called",
        {"tool_name": "cortex", "turn": 1, "elapsed_ms": 12.3, "provider": "anthropic"},
    )
    cb(
        "pipeline.frontier.dispatch.tool.failed",
        {"tool_name": "rag_search", "turn": 2, "elapsed_ms": 5.0, "provider": "openai"},
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

    monkeypatch.setattr(fd_mod, "run_native_tool_loop", fake_loop)

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

    monkeypatch.setattr(fd_mod, "run_native_tool_loop", fake_loop)

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

    async def fake_hydrate(agent: str, transcript_id: str | None) -> _FakeBundle:
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

    monkeypatch.setattr(fd_mod, "hydrate_agent", fake_hydrate)
    monkeypatch.setattr(fd_mod, "assemble_system_prompt", fake_assemble)
    monkeypatch.setattr(fd_mod, "get_mcp_tool_definitions", fake_defs)
    monkeypatch.setattr(fd_mod, "run_native_tool_loop", fake_loop)

    step = _FakeStep()
    context = _make_context(options={"model": "openai/gpt-5.4", "agent": "orion"})
    await handler.execute(step, context)

    assert [t["function"]["name"] for t in captured["tools"]] == ["web_search"]


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

    monkeypatch.setattr(fd_mod, "resolve_tool_definitions", fake_resolve)
    monkeypatch.setattr(fd_mod, "run_native_tool_loop", fake_loop)

    step = _FakeStep()
    context = _make_context(
        options={"model": "openai/gpt-5.4", "tools": ["web_search"]}
    )
    await handler.execute(step, context)

    assert [t["function"]["name"] for t in captured["tools"]] == ["web_search"]


def test_resolve_remote_mcp_defaults_by_provider() -> None:
    h = FrontierDispatchHandler()
    step = _FakeStep()
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
        result = h._resolve_remote_mcp(
            opts=context.options,
            step=step,
            context=context,
            provider=provider,
            model=model,
            agent=None,
            mcp_enabled=mcp_enabled,
        )
        assert result is expected, (
            f"{provider} mcp={mcp_enabled}: expected {expected}, got {result}"
        )


def test_resolve_agent_uses_options_then_domain_field() -> None:
    h = FrontierDispatchHandler()
    step_with_domain = _FakeStep(domain_fields={"agent": "bard"})
    assert h._resolve_agent({}, step_with_domain) == "bard"
    assert h._resolve_agent({"agent": "web"}, step_with_domain) == "web"
    assert h._resolve_agent({"agent": ""}, step_with_domain) == "bard"
    assert h._resolve_agent({}, _FakeStep()) is None


# ---------------------------------------------------------------------------
# Admission guard tests
# ---------------------------------------------------------------------------


def test_reject_unknown_runtime_options_raises_on_unknown_keys(
    handler: FrontierDispatchHandler,
) -> None:
    """Unknown ``runtime_options`` keys must raise ``UnknownPipelineOptionsError``."""
    from systems.pipeline.core.execution.errors import UnknownPipelineOptionsError

    step = _FakeStep()
    context = _make_context(
        options={"model": "anthropic/claude-sonnet-4-6"},
        runtime_options={"unknown_key": True, "another_bad_key": 42},
    )
    with pytest.raises(UnknownPipelineOptionsError) as exc_info:
        handler._reject_unknown_runtime_options(step, context)
    assert "unknown_key" in str(exc_info.value)


def test_reject_unknown_runtime_options_passes_on_accepted_keys(
    handler: FrontierDispatchHandler,
) -> None:
    """All keys in ``_ACCEPTED_RUNTIME_OPTION_KEYS`` must be admitted without error."""
    step = _FakeStep()
    context = _make_context(
        options={"model": "anthropic/claude-sonnet-4-6"},
        runtime_options={
            k: True for k in FrontierDispatchHandler._ACCEPTED_RUNTIME_OPTION_KEYS
        },
    )
    handler._reject_unknown_runtime_options(step, context)


def test_check_agent_model_consistency_rejects_mismatch() -> None:
    """agent/model provider mismatch raises AgentModelMismatchError."""
    from systems.pipeline.core.execution.errors import AgentModelMismatchError
    from systems.pipeline.core.handlers.frontier_dispatch_admission import (
        check_agent_model_consistency,
    )

    published: list[Any] = []

    with pytest.raises(AgentModelMismatchError) as exc_info:
        check_agent_model_consistency(
            agent="oppie",
            model="anthropic/claude-sonnet-4-6",
            provider="anthropic",
            execution_id="exec-test-0001",
            publish=published.append,
        )

    err = exc_info.value
    assert err.agent == "oppie"
    assert err.provider == "anthropic"
    assert err.expected_provider == "xai"
    assert err.to_dict()["code"] == "agent_model_mismatch"
    assert len(published) == 1
    assert published[0].signal == "pipeline.frontier.dispatch.mismatch"
    assert published[0].payload["agent"] == "oppie"
    assert published[0].payload["requested_model"] == "anthropic/claude-sonnet-4-6"


def test_check_agent_model_consistency_accepts_valid_family() -> None:
    """Matching agent/provider must not raise or emit an event."""
    from systems.pipeline.core.handlers.frontier_dispatch_admission import (
        check_agent_model_consistency,
    )

    published: list[Any] = []

    check_agent_model_consistency(
        agent="oppie",
        model="xai/grok-4-fast-reasoning",
        provider="xai",
        execution_id="exec-test-0001",
        publish=published.append,
    )

    assert published == []


def test_check_agent_model_consistency_passes_unknown_agent() -> None:
    """Unknown agents (not in registry) are not checked — custom slugs are allowed."""
    from systems.pipeline.core.handlers.frontier_dispatch_admission import (
        check_agent_model_consistency,
    )

    published: list[Any] = []

    check_agent_model_consistency(
        agent="custom-bot",
        model="anthropic/claude-sonnet-4-6",
        provider="anthropic",
        execution_id="exec-test-0001",
        publish=published.append,
    )

    assert published == []
