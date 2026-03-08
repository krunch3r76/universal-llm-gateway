"""Targeted tests for generate-step model selection behavior."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from .execution import requirements_resolver
from .handlers.generate import GenericGenerateHandler
from .pipeline_config import PromptConfig
from .step_config import StepConfig


def _build_context(*, model_ref_overrides: dict[str, str] | None = None) -> MagicMock:
    """Build the minimal PipelineContext surface needed by GenericGenerateHandler."""
    registry = MagicMock()
    registry.get_prompt.return_value = PromptConfig(
        name="consult.architect",
        template="{text}",
    )
    context = MagicMock(_registry=registry)
    context.options = {}
    if model_ref_overrides:
        context.options["model_ref_overrides"] = model_ref_overrides
    context._step_model_override = {}
    context.pipeline = MagicMock(domain="consult", source_search_path=[])
    context.source_text = "quick health check"
    return context


def _build_handler() -> GenericGenerateHandler:
    """Create a handler with expensive internals stubbed out."""
    handler = GenericGenerateHandler()
    handler._render_user_prompt = MagicMock(return_value="quick health check")
    handler._extract_source_provenance = MagicMock(return_value=None)
    handler._invoke_model = AsyncMock(return_value=MagicMock(name="step_output"))
    return handler


@pytest.mark.asyncio
async def test_generate_handler_explicit_override_bypasses_auto_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Runtime overrides must win before auto requirements resolution."""
    step = StepConfig.model_validate(
        {
            "id": "consult",
            "type": "generate",
            "model_ref": "auto",
            "prompt_ref": "consult.architect",
            "model_requirements": {
                "task": "code_architecture",
                "source": "cloud",
            },
        }
    )
    context = _build_context(model_ref_overrides={"consult": "openai/o3"})
    handler = _build_handler()

    def _fail_requirements_resolution(*_args, **_kwargs):
        raise AssertionError("auto resolution should be bypassed by --models override")

    monkeypatch.setattr(
        requirements_resolver,
        "resolve_model_requirements",
        _fail_requirements_resolution,
    )

    result = await handler.execute(step, context)

    assert result is handler._invoke_model.return_value
    handler._invoke_model.assert_awaited_once()
    assert handler._invoke_model.await_args.args[3] == "openai/o3"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source", "resolved_model_id"),
    [
        ("cloud", "openai/o3"),
        ("any", "qwen3-32b-awq-32768"),
    ],
)
async def test_generate_handler_auto_resolution_uses_returned_candidate(
    monkeypatch: pytest.MonkeyPatch,
    source: str,
    resolved_model_id: str,
) -> None:
    """Auto mode should still resolve and use the first returned candidate."""
    seen_requirements: list[dict[str, object]] = []
    step = StepConfig.model_validate(
        {
            "id": "consult",
            "type": "generate",
            "model_ref": "auto",
            "prompt_ref": "consult.architect",
            "model_requirements": {
                "task": "code_architecture" if source == "cloud" else "research",
                "source": source,
            },
        }
    )
    context = _build_context()
    handler = _build_handler()

    def _resolve(requirements: dict[str, object]) -> list[str]:
        seen_requirements.append(dict(requirements))
        return [resolved_model_id]

    monkeypatch.setattr(requirements_resolver, "resolve_model_requirements", _resolve)

    result = await handler.execute(step, context)

    assert result is handler._invoke_model.return_value
    assert seen_requirements == [dict(step.model_requirements or {})]
    handler._invoke_model.assert_awaited_once()
    assert handler._invoke_model.await_args.args[3] == resolved_model_id


def test_requirements_resolver_logs_timeout_context(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Timeouts should log task and payload so empty results are diagnosable."""

    class _TimeoutClient:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs

        def __enter__(self) -> _TimeoutClient:
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            del exc_type, exc, tb
            return False

        def post(self, url: str, json: dict[str, object]):
            del url, json
            raise httpx.ReadTimeout("selection endpoint too slow")

    monkeypatch.setattr(httpx, "Client", _TimeoutClient)

    with caplog.at_level(logging.ERROR):
        result = requirements_resolver.resolve_model_requirements(
            {
                "task": "code_architecture",
                "source": "cloud",
                "tags": ["general"],
            }
        )

    assert result == []
    assert "timed out after" in caplog.text
    assert "code_architecture" in caplog.text
    assert "'tags': ['general']" in caplog.text
