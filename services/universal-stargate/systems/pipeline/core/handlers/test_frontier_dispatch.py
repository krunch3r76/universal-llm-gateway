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
) -> SimpleNamespace:
    """Return a stub PipelineContext covering the fields the handler touches."""
    return SimpleNamespace(
        execution_id="exec-test-0001",
        source_text=source_text,
        messages=messages,
        options=options or {},
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
        return _FakeLoopResult()

    monkeypatch.setattr(fd_mod, "hydrate_agent", fake_hydrate)
    monkeypatch.setattr(fd_mod, "assemble_system_prompt", fake_assemble)
    monkeypatch.setattr(fd_mod, "run_native_tool_loop", fake_loop)

    step = _FakeStep()
    context = _make_context(
        options={"model": "anthropic/claude-sonnet-4-6", "agent": "oppie"}
    )

    out = await handler.execute(step, context)

    signals = [e.signal for e in published_events]
    assert "pipeline.frontier.dispatch.hydrated" in signals
    assert "pipeline.frontier.dispatch.completed" in signals
    assert out.system_prompt == "SYSTEM[oppie]"
    assert out.json["hydration"]["agent"] == "oppie"
    assert out.json["provider"] == "anthropic"


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
        options={"model": "openai/gpt-5.4", "inject_tools": False},
    )

    out = await handler.execute(step, context)

    assert hydrate_calls == []
    signals = [e.signal for e in published_events]
    assert "pipeline.frontier.dispatch.hydrated" not in signals
    assert "pipeline.frontier.dispatch.completed" in signals
    assert out.system_prompt == "You are a test assistant."
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


def test_resolve_agent_uses_options_then_domain_field() -> None:
    h = FrontierDispatchHandler()
    step_with_domain = _FakeStep(domain_fields={"agent": "bard"})
    assert h._resolve_agent({}, step_with_domain) == "bard"
    assert h._resolve_agent({"agent": "web"}, step_with_domain) == "web"
    assert h._resolve_agent({"agent": ""}, step_with_domain) == "bard"
    assert h._resolve_agent({}, _FakeStep()) is None
