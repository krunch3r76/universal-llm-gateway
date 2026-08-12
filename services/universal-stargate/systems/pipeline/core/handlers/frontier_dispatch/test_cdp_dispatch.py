"""CDP substrate branch for frontier_dispatch_v1 (Option 3)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from claude_bundles.cdp_model_endpoint import CdpGenerateResult

from systems.pipeline.core.handlers.frontier_dispatch import (
    FrontierDispatchHandler,
)
from systems.pipeline.core.handlers.frontier_dispatch import (
    cdp_dispatch as cdp_mod,
)
from systems.pipeline.core.handlers.frontier_dispatch import (
    native_loop as fd_native_mod,
)
from systems.pipeline.core.handlers.frontier_dispatch.cdp_dispatch import (
    build_cdp_step_output,
    compose_cdp_prompt_text,
    is_cdp_dispatch_model,
    parse_cdp_harvest_options,
    run_cdp_dispatch,
)


def test_is_cdp_dispatch_model() -> None:
    assert is_cdp_dispatch_model("cdp/opus-5") is True
    assert is_cdp_dispatch_model("cdp/sonnet-5") is True
    assert is_cdp_dispatch_model("openai/gpt-5") is False
    assert is_cdp_dispatch_model("cursor/grok-4.5") is False


def test_cdp_picker_forwarded_for_sonnet_5() -> None:
    from claude_bundles.cdp_model_endpoint import picker_from_model_id

    assert picker_from_model_id("cdp/sonnet-5") == "sonnet-5"
    assert picker_from_model_id("cdp/opus-5") == "opus-5"


def test_parse_cdp_harvest_options_defaults() -> None:
    parsed = parse_cdp_harvest_options({})
    assert parsed["harvest_source"] == "auto"
    assert parsed["expected_size"] == "auto"
    assert parsed["download_output"] is False


def test_parse_cdp_harvest_options_forwarded() -> None:
    parsed = parse_cdp_harvest_options(
        {
            "harvest_source": "chat",
            "expected_size": "large",
            "download_output": True,
            "timeout_seconds": 120,
        }
    )
    assert parsed["harvest_source"] == "chat"
    assert parsed["expected_size"] == "large"
    assert parsed["download_output"] is True
    assert parsed["max_wall_s"] == 120.0


def test_compose_cdp_prompt_text() -> None:
    assert compose_cdp_prompt_text("user", "system") == "system\n\n---\n\nuser"
    assert compose_cdp_prompt_text("only user", None) == "only user"


def test_build_cdp_step_output_dual_bind() -> None:
    result = CdpGenerateResult(
        ok=True,
        body="inline answer",
        execution_id="exec-1",
        satellite_execution_id="sat-1",
        prompt_uri="cortex://notes/system/threads/exec-1-prompt.md",
        picker_model="opus-5",
        archive_uri="cortex://archive/a.md",
        content_proof_uri="cortex://proof/p.md",
        content_proof_sha256="abc",
        extras={"harvest_provenance": {"source": "chat"}},
    )
    step = SimpleNamespace(id="respond")
    admission = SimpleNamespace(
        model="cdp/opus-5",
        model_entity_id="model:cdp-opus-5",
        user_prompt="question",
    )
    output = build_cdp_step_output(
        result=result,
        step=step,
        admission=admission,
        latency_ms=42.0,
        system_prompt="sys",
    )
    assert output.raw == "inline answer"
    assert output.json is not None
    assert output.json["content"] == "inline answer"
    assert output.json["archive_uri"] == "cortex://archive/a.md"
    assert output.json["content_proof_uri"] == "cortex://proof/p.md"
    assert output.json["content_proof_sha256"] == "abc"
    assert output.json["harvest_provenance"] == {"source": "chat"}


@pytest.mark.asyncio
async def test_handler_cdp_model_runs_adapter_not_native_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    published: list[Any] = []
    handler = FrontierDispatchHandler()
    monkeypatch.setattr(
        handler,
        "_publish_bus_event",
        lambda _ctx, event: published.append(event),
    )

    captured: dict[str, Any] = {}

    def fake_run_cdp_generate(**kwargs: Any) -> CdpGenerateResult:
        captured.update(kwargs)
        return CdpGenerateResult(
            ok=True,
            body="cdp body",
            execution_id=kwargs["execution_id"],
            satellite_execution_id="sat-99",
            prompt_uri="cortex://notes/system/threads/exec-cdp-prompt.md",
            picker_model="opus-5",
            content_proof_uri="cortex://proof/only.md",
        )

    native_called = False

    async def fake_native_loop(**_k: Any) -> Any:
        nonlocal native_called
        native_called = True
        raise AssertionError("native loop should not run for cdp/")

    monkeypatch.setattr(cdp_mod, "run_cdp_generate", fake_run_cdp_generate)
    monkeypatch.setattr(
        "systems.frontier_consult.cdp_events.publish_cdp_kwargs",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(fd_native_mod, "run_dispatch_loop", fake_native_loop)

    step = SimpleNamespace(
        id="respond",
        name="respond",
        system_prompt=None,
        handler_inputs={},
        generation_parameters={},
        _domain={},
        get_domain_field=lambda key, default=None: default,
    )
    context = SimpleNamespace(
        execution_id="exec-cdp",
        source_text="pipeline question",
        messages=None,
        options={
            "model": "cdp/opus-5",
            "harvest_source": "chat",
            "expected_size": "small",
        },
        runtime_options={},
        _registry=None,
        pipeline=SimpleNamespace(domain=None, source_search_path=None),
        _proxy=SimpleNamespace(pipeline_dispatch_tracker=None, event_bus=None),
    )

    out = await handler.execute(step, context)

    assert native_called is False
    assert captured["model_id"] == "cdp/opus-5"
    assert captured["converse"] is True
    assert captured["harvest_source"] == "chat"
    assert captured["expected_size"] == "small"
    assert out.raw == "cdp body"
    assert out.json is not None
    assert out.json["content"] == "cdp body"
    assert out.json["content_proof_uri"] == "cortex://proof/only.md"
    signals = [e.signal for e in published]
    assert "pipeline.frontier.dispatch.started" in signals
    assert "pipeline.frontier.dispatch.completed" in signals


@pytest.mark.asyncio
async def test_handler_cdp_forwards_pipeline_skills(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handler = FrontierDispatchHandler()
    monkeypatch.setattr(handler, "_publish_bus_event", lambda *_a, **_k: None)
    captured: dict[str, Any] = {}

    def fake_run_cdp_generate(**kwargs: Any) -> CdpGenerateResult:
        captured.update(kwargs)
        return CdpGenerateResult(
            ok=True,
            body="ok",
            execution_id=kwargs["execution_id"],
            satellite_execution_id="sat-skills",
            prompt_uri="cortex://notes/system/ephemeral/x/prompt.md",
            picker_model="opus-5",
            content_proof_uri="cortex://proof/p.md",
        )

    monkeypatch.setattr(cdp_mod, "run_cdp_generate", fake_run_cdp_generate)
    monkeypatch.setattr(
        "systems.frontier_consult.cdp_events.publish_cdp_kwargs",
        lambda *_a, **_k: None,
    )

    step = SimpleNamespace(
        id="respond",
        name="respond",
        system_prompt=None,
        handler_inputs={},
        generation_parameters={},
        _domain={},
        get_domain_field=lambda key, default=None: default,
    )
    context = SimpleNamespace(
        execution_id="exec-cdp-skills",
        source_text="pipeline question",
        messages=None,
        options={
            "model": "cdp/opus-5",
            "skills": ["reasoning-posture", "consult-posture"],
        },
        runtime_options={},
        _registry=None,
        pipeline=SimpleNamespace(domain=None, source_search_path=None),
        _proxy=SimpleNamespace(pipeline_dispatch_tracker=None, event_bus=None),
    )

    await handler.execute(step, context)
    assert captured["skills"] == ["reasoning-posture", "consult-posture"]


@pytest.mark.asyncio
async def test_run_cdp_dispatch_forwards_since_last_progress_s_on_stall(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stalled_calls: list[dict[str, Any]] = []

    def capture_stalled(_factory, **kwargs: Any) -> None:
        stalled_calls.append(kwargs)

    monkeypatch.setattr(
        "systems.frontier_consult.cdp_events.publish_cdp_kwargs",
        capture_stalled,
    )
    monkeypatch.setattr(
        cdp_mod,
        "run_cdp_generate",
        lambda **_kwargs: CdpGenerateResult(
            ok=False,
            body="",
            execution_id="exec-stall",
            satellite_execution_id="sat-stall",
            prompt_uri="cortex://notes/system/ephemeral/x/prompt.md",
            picker_model="opus-5",
            stall_stage="wall_clock_exceeded",
            error="no progress",
            extras={"since_last_progress_s": 123.4},
        ),
    )

    step = SimpleNamespace(
        id="respond",
        name="respond",
        system_prompt=None,
        handler_inputs={},
        generation_parameters={},
        _domain={},
        get_domain_field=lambda key, default=None: default,
    )
    admission = SimpleNamespace(
        model="cdp/opus-5",
        model_entity_id="model:cdp-opus-5",
        user_prompt="question",
        system=None,
        opts={},
        publish=lambda _event: None,
    )
    context = SimpleNamespace(execution_id="exec-stall")

    with pytest.raises(Exception, match="CDP dispatch failed"):
        await run_cdp_dispatch(
            handler=SimpleNamespace(),
            step=step,
            context=context,
            admission=admission,
        )

    assert stalled_calls
    assert stalled_calls[0]["since_last_progress_s"] == 123.4
    assert "active_wall_s" not in stalled_calls[0]
    assert "wall_paused_s" not in stalled_calls[0]
