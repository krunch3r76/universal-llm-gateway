"""GenericGenerateHandler streaming-branch tests.

Covers Phase 3 of ``plan:pipeline-terminal-passthrough-streaming``: verifies
the streaming branch fires iff::

    outer_stream ∧ pipeline.is_stream_passthrough_eligible ∧ ¬step.is_map_step

Three of the four ``(eligible, outer_stream)`` combinations fall through to
the buffered path; only the all-True combination invokes the streaming path.
The defensive ``not step.is_map_step`` check is exercised separately.

Branch selection is asserted by patching the module-level
``invoke_model_streaming`` and the class-level ``_invoke_model`` method —
whichever fires marks itself in a probe dict so the test can verify the
correct path was taken without exercising the full call stack.

Companion sidecar:
``cortex:notes/system/threads/pipeline-terminal-passthrough-streaming-arc-phase-3.md``.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from systems.pipeline.core.handlers.generate import handler as handler_mod
from systems.pipeline.core.handlers.generate.handler import GenericGenerateHandler
from systems.pipeline.core.handlers.protocol import StepOutput


class _NoopStream:
    """AsyncIterator that yields nothing — see test_protocol.py for rationale."""

    def __aiter__(self) -> _NoopStream:
        return self

    async def __anext__(self) -> dict[str, Any]:
        raise StopAsyncIteration


class _FakeStep:
    """Minimal StepConfig surface for streaming-branch tests.

    Carries only the fields handler.execute reads before the streaming
    branch fires: ``name`` / ``id``, ``is_map_step``, ``prompt_ref``.
    """

    def __init__(self, *, name: str = "respond", is_map_step: bool = False) -> None:
        self.name = name
        self.is_map_step = is_map_step
        self.prompt_ref = "respond_v1"

    @property
    def id(self) -> str:
        return self.name


class _FakePipeline:
    def __init__(self, *, is_stream_passthrough_eligible: bool) -> None:
        self.is_stream_passthrough_eligible = is_stream_passthrough_eligible


class _FakePromptConfig:
    """Minimal PromptConfig stub — handler stubs short-circuit before any
    template fields are read; included for surface completeness."""

    system_prompt = ""
    user_template = "{text}"


def _make_context(
    *,
    pipeline: _FakePipeline,
    runtime_options: dict[str, Any] | None = None,
) -> SimpleNamespace:
    """Stub PipelineContext covering only the fields handler.execute reads
    before branching: ``_registry``, ``pipeline``, ``runtime_options``,
    ``_step_model_override``.
    """
    return SimpleNamespace(
        pipeline=pipeline,
        runtime_options=runtime_options or {},
        _registry=SimpleNamespace(get_prompt=lambda _ref: _FakePromptConfig()),
        _step_model_override={},
    )


def _patch_resolve_and_render(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub out everything ``execute()`` calls before the branch check.

    The streaming branch fires after ``resolve_primary_model`` returns and
    before either ``invoke_model_streaming`` or ``_invoke_model`` is called.
    These patches replace the three pre-branch hooks with no-op stubs so
    branch-selection assertions are not perturbed by registry / prompt /
    routing side effects.
    """

    async def fake_resolve_primary_model(
        _step: Any, _context: Any
    ) -> tuple[str, None, Any]:
        return ("openai/gpt-5.4", None, SimpleNamespace(reason="test"))

    def fake_render_user_prompt(_self: Any, _pc: Any, _step: Any, _ctx: Any) -> str:
        return "hello"

    def fake_extract_source_provenance(_self: Any, _step: Any, _ctx: Any) -> None:
        return None

    monkeypatch.setattr(
        handler_mod, "resolve_primary_model", fake_resolve_primary_model
    )
    monkeypatch.setattr(
        GenericGenerateHandler, "_render_user_prompt", fake_render_user_prompt
    )
    monkeypatch.setattr(
        GenericGenerateHandler,
        "_extract_source_provenance",
        fake_extract_source_provenance,
    )


def _install_branch_probes(monkeypatch: pytest.MonkeyPatch) -> dict[str, bool]:
    """Patch streaming + buffered invocation paths to record which fires."""
    fired = {"streaming": False, "buffered": False}

    async def fake_streaming(*_args: Any, **_kwargs: Any) -> StepOutput:
        fired["streaming"] = True
        return StepOutput(
            raw="",
            stream=_NoopStream(),
            model_id="openai/gpt-5.4",
            step_id="respond",
        )

    async def fake_buffered(
        _self: Any, _step: Any, _ctx: Any, *_args: Any, **_kwargs: Any
    ) -> StepOutput:
        fired["buffered"] = True
        return StepOutput(
            raw="buffered-content",
            model_id="openai/gpt-5.4",
            step_id="respond",
        )

    monkeypatch.setattr(handler_mod, "invoke_model_streaming", fake_streaming)
    monkeypatch.setattr(GenericGenerateHandler, "_invoke_model", fake_buffered)
    return fired


@pytest.mark.asyncio
async def test_handler_uses_buffered_path_when_outer_stream_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Eligible pipeline + no outer ``stream`` flag → buffered path.

    The streaming branch requires BOTH outer-stream and eligibility; either
    one alone falls through to the existing buffered ``_invoke_model`` path.
    """
    _patch_resolve_and_render(monkeypatch)
    fired = _install_branch_probes(monkeypatch)

    handler = GenericGenerateHandler()
    step = _FakeStep()
    context = _make_context(
        pipeline=_FakePipeline(is_stream_passthrough_eligible=True),
        runtime_options={},
    )

    out = await handler.execute(step, context)

    assert fired == {"streaming": False, "buffered": True}
    assert out.stream is None
    assert out.raw == "buffered-content"


@pytest.mark.asyncio
async def test_handler_uses_buffered_path_when_pipeline_ineligible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Outer ``stream=True`` + ineligible pipeline → buffered path.

    Mirror of the prior test: the eligibility predicate (Phase 1) is the
    second of the two required signals; without it the buffered fallback
    applies even when the client requested streaming.
    """
    _patch_resolve_and_render(monkeypatch)
    fired = _install_branch_probes(monkeypatch)

    handler = GenericGenerateHandler()
    step = _FakeStep()
    context = _make_context(
        pipeline=_FakePipeline(is_stream_passthrough_eligible=False),
        runtime_options={"stream": True},
    )

    out = await handler.execute(step, context)

    assert fired == {"streaming": False, "buffered": True}
    assert out.stream is None


@pytest.mark.asyncio
async def test_handler_uses_streaming_path_when_eligible_and_outer_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Outer ``stream=True`` + eligible pipeline + non-map step → streaming path.

    The all-True combination is the only one that fires the streaming branch.
    The returned StepOutput carries the streaming invariant: ``stream`` is
    set, ``raw`` is empty, ``model_id`` reflects the resolved model.
    """
    _patch_resolve_and_render(monkeypatch)
    fired = _install_branch_probes(monkeypatch)

    handler = GenericGenerateHandler()
    step = _FakeStep(is_map_step=False)
    context = _make_context(
        pipeline=_FakePipeline(is_stream_passthrough_eligible=True),
        runtime_options={"stream": True},
    )

    out = await handler.execute(step, context)

    assert fired == {"streaming": True, "buffered": False}
    assert out.stream is not None
    assert out.raw == ""
    assert out.model_id == "openai/gpt-5.4"


@pytest.mark.asyncio
async def test_handler_streaming_path_skipped_for_map_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Map step + otherwise-eligible signals → defensive fall-through to buffered.

    The Phase 1 eligibility predicate already excludes map steps, so this
    combination is structurally unreachable in production. The defensive
    ``not step.is_map_step`` check at the branch site documents the
    invariant locally and protects against future predicate drift.
    """
    _patch_resolve_and_render(monkeypatch)
    fired = _install_branch_probes(monkeypatch)

    handler = GenericGenerateHandler()
    step = _FakeStep(is_map_step=True)
    context = _make_context(
        pipeline=_FakePipeline(is_stream_passthrough_eligible=True),
        runtime_options={"stream": True},
    )

    out = await handler.execute(step, context)

    assert fired == {"streaming": False, "buffered": True}
    assert out.stream is None
