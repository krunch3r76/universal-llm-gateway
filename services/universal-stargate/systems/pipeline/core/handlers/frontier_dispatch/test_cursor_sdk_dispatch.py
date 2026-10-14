"""Cursor SDK substrate branch for frontier_dispatch_v1 (journal-projector L2)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from systems.pipeline.core.handlers.frontier_dispatch import (
    FrontierDispatchHandler,
)
from systems.pipeline.core.handlers.frontier_dispatch import (
    native_loop as fd_native_mod,
)
from systems.pipeline.core.handlers.frontier_dispatch.cursor_sdk_dispatch import (
    is_cursor_sdk_dispatch_model,
)


def test_is_cursor_sdk_dispatch_model() -> None:
    assert is_cursor_sdk_dispatch_model("cursor/composer-2.5") is True
    assert is_cursor_sdk_dispatch_model("openai/gpt-5") is False
    assert is_cursor_sdk_dispatch_model("cdp/opus-5") is False


@pytest.mark.asyncio
async def test_cursor_model_skips_cloud_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Acceptance 3: cursor/composer-2.5 uses SDK generate, not cloud loop."""
    handler = FrontierDispatchHandler()
    monkeypatch.setattr(handler, "_publish_bus_event", lambda *_a, **_k: None)

    response_constructed = False

    def _track_response(*_a: Any, **_k: Any) -> None:
        nonlocal response_constructed
        response_constructed = True

    try:
        from fastapi import Response

        monkeypatch.setattr(Response, "__init__", _track_response, raising=False)
    except ImportError:
        pass

    captured: dict[str, Any] = {}

    async def fake_sdk_generate(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {
            "execution_id": "exec-cursor-1",
            "poll_hint": {
                "tool": "wait",
                "arguments": {"thread": "12286", "from_agent": "cursor-sdk"},
            },
        }

    native_called = False

    async def fake_native_loop(**_k: Any) -> Any:
        nonlocal native_called
        native_called = True
        raise AssertionError("native loop should not run for cursor_sdk")

    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_generate.dispatch_cursor_sdk_generate",
        fake_sdk_generate,
    )
    monkeypatch.setattr(fd_native_mod, "run_dispatch_loop", fake_native_loop)

    step = SimpleNamespace(
        id="project",
        name="project",
        system_prompt=None,
        handler_inputs={},
        generation_parameters={},
        _domain={},
        get_domain_field=lambda key, default=None: default,
    )
    context = SimpleNamespace(
        execution_id="exec-cursor-1",
        source_text="project the lesson",
        messages=None,
        options={"model": "cursor/composer-2.5", "model_knobs": {"fast": "true"}},
        runtime_options={},
        _registry=None,
        pipeline=SimpleNamespace(domain=None, source_search_path=None),
        _proxy=SimpleNamespace(pipeline_dispatch_tracker=None, event_bus=None),
    )

    out = await handler.execute(step, context)

    assert native_called is False
    assert response_constructed is False
    assert captured["model"] == "cursor/composer-2.5"
    assert captured["lane"] == "B"
    assert captured.get("model_knobs") == {"fast": "true"}
    assert out.json is not None
    assert out.json["execution_id"] == "exec-cursor-1"
    assert out.json["poll_hint"]["tool"] == "wait"


@pytest.mark.asyncio
async def test_model_knobs_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    """model_knobs must pass admission (else UnknownPipelineOptionsError)."""
    handler = FrontierDispatchHandler()
    monkeypatch.setattr(handler, "_publish_bus_event", lambda *_a, **_k: None)

    async def fake_sdk_generate(**_kwargs: Any) -> dict[str, Any]:
        return {
            "execution_id": "e-knobs",
            "poll_hint": {"tool": "wait", "arguments": {}},
        }

    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_generate.dispatch_cursor_sdk_generate",
        fake_sdk_generate,
    )

    step = SimpleNamespace(
        id="project",
        name="project",
        system_prompt=None,
        handler_inputs={},
        generation_parameters={},
        _domain={},
        get_domain_field=lambda key, default=None: default,
    )
    context = SimpleNamespace(
        execution_id="e-knobs",
        source_text="hi",
        messages=None,
        options={"model": "cursor/composer-2.5", "model_knobs": {"fast": "true"}},
        runtime_options={},
        _registry=None,
        pipeline=SimpleNamespace(domain=None, source_search_path=None),
        _proxy=SimpleNamespace(pipeline_dispatch_tracker=None, event_bus=None),
    )

    out = await handler.execute(step, context)
    assert out.json is not None
    assert out.json["execution_id"] == "e-knobs"

