"""Tests for the `parse_json_v1` deterministic builtin step handler.

Validates the (γ) resolution path from thread 759: structured cross-step
JSON binding via an explicit parse step inserted between a JSON-string
producer (frontier_dispatch_v1, pipeline_call_v1) and a consumer that
needs real dicts/lists at the binding site.

Tests run against a real `NamespaceResolver` with a stubbed context — the
handler's behavior is end-to-end exercised, including binding resolution
and `traverse_path` navigation, so wiring regressions surface here.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from systems.pipeline.core.handlers.parse_json import ParseJsonV1Handler
from systems.pipeline.core.handlers.protocol import StepOutput
from systems.pipeline.core.step_types import InputBinding


class _FakeStep:
    """Minimal StepConfig surface — only fields the handler reads."""

    def __init__(
        self,
        *,
        name: str = "parsed",
        handler_inputs: dict[str, InputBinding] | None = None,
    ) -> None:
        self.name = name
        self.type = "parse_json_v1"
        self.handler_inputs: dict[str, InputBinding] = handler_inputs or {}

    @property
    def id(self) -> str:
        return self.name


def _step_binding(step_name: str, field_path: str) -> InputBinding:
    return InputBinding(
        namespace="step",
        step_name=step_name,
        field_path=field_path,
    )


def _make_context(*, outputs: dict[str, StepOutput] | None = None) -> Any:
    """Stub PipelineContext exposing the surface NamespaceResolver consumes.

    The resolver instantiates SourceNamespaceHandler / OptionsNamespaceHandler
    eagerly even when the binding only uses `step.*`, so `source` and
    `options` must be present on the context — but their content is unused
    in these tests.
    """
    return SimpleNamespace(
        outputs=outputs or {},
        source=SimpleNamespace(text="", messages=None),
        options={},
    )


@pytest.mark.asyncio
async def test_parse_json_v1_parses_dict_from_step_binding() -> None:
    """Happy path: JSON-string at `step.json.content` → `StepOutput.json`."""
    upstream = StepOutput(
        raw='{"brief_markdown": "# Brief", "questions": ["q1", "q2"]}',
        json={
            "content": '{"brief_markdown": "# Brief", "questions": ["q1", "q2"]}',
            "tool_calls_made": 0,
        },
    )
    step = _FakeStep(
        handler_inputs={"source": _step_binding("brief_compose", "json.content")},
    )
    context = _make_context(outputs={"brief_compose": upstream})

    handler = ParseJsonV1Handler()
    output = await handler.execute(step, context)

    assert output.json == {
        "brief_markdown": "# Brief",
        "questions": ["q1", "q2"],
    }
    assert output.raw == upstream.json["content"]


@pytest.mark.asyncio
async def test_parse_json_v1_raises_value_error_on_invalid_json() -> None:
    """Malformed JSON surfaces a clear error with line/col + snippet."""
    upstream = StepOutput(
        raw="not valid json {",
        json={"content": "not valid json {"},
    )
    step = _FakeStep(
        handler_inputs={"source": _step_binding("upstream", "json.content")},
    )
    context = _make_context(outputs={"upstream": upstream})

    handler = ParseJsonV1Handler()
    with pytest.raises(ValueError, match="parse_json_v1 failed to parse JSON"):
        await handler.execute(step, context)


@pytest.mark.asyncio
async def test_parse_json_v1_rejects_top_level_array() -> None:
    """Top-level arrays break the `step.json.<field>` binding contract — fail loudly."""
    upstream = StepOutput(
        raw='[{"id": 1}, {"id": 2}]',
        json={"content": '[{"id": 1}, {"id": 2}]'},
    )
    step = _FakeStep(
        handler_inputs={"source": _step_binding("upstream", "json.content")},
    )
    context = _make_context(outputs={"upstream": upstream})

    handler = ParseJsonV1Handler()
    with pytest.raises(ValueError, match="expected a JSON object"):
        await handler.execute(step, context)


@pytest.mark.asyncio
async def test_parse_json_v1_rejects_non_string_source() -> None:
    """Non-string `source` (e.g. binding to an already-parsed list) fails fast."""
    upstream = StepOutput(
        raw="ignored",
        json={"data": [1, 2, 3]},
    )
    step = _FakeStep(
        handler_inputs={"source": _step_binding("upstream", "json.data")},
    )
    context = _make_context(outputs={"upstream": upstream})

    handler = ParseJsonV1Handler()
    with pytest.raises(ValueError, match="expected string"):
        await handler.execute(step, context)


@pytest.mark.asyncio
async def test_parse_json_v1_raises_when_source_binding_missing() -> None:
    """Missing `handler_inputs.source` fails at execute time with a clear message."""
    step = _FakeStep(handler_inputs={})
    context = _make_context()

    handler = ParseJsonV1Handler()
    with pytest.raises(ValueError, match="requires handler_inputs.source"):
        await handler.execute(step, context)


def test_parse_json_v1_validate_flags_missing_source() -> None:
    step = _FakeStep(handler_inputs={})
    handler = ParseJsonV1Handler()

    errors = handler.validate(step)

    assert len(errors) == 1
    assert "handler_inputs.source" in errors[0]


def test_parse_json_v1_validate_passes_when_source_present() -> None:
    step = _FakeStep(
        handler_inputs={"source": _step_binding("upstream", "json.content")},
    )
    handler = ParseJsonV1Handler()

    assert handler.validate(step) == []
