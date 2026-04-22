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


def _bundle(meta: AgentMeta) -> HydrationBundle:
    return HydrationBundle(briefing_card_md="# briefing", agent_meta=meta)


@pytest.mark.asyncio
async def test_permissive_persona_accepts_anything(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_hydrate(agent: str, transcript_id: str | None) -> HydrationBundle:
        return _bundle(
            AgentMeta(
                default_model="openai/gpt-5.4-mini",
                allowed_models=[],
                tools=None,
                allowed_options=None,
            )
        )

    monkeypatch.setattr(
        "systems.frontier_consult.service.hydrate_agent",
        fake_hydrate,
    )

    req = FrontierGenerateRequest(
        messages=[{"role": "user", "content": "hello"}],
        agent="orion",
        tools=["cortex"],
        generation_options={"max_tokens": 12, "temperature": 0.2},
    )
    body = await build_dispatch_body(req)
    options = body["pipeline_options"]
    assert body["model"] == "frontier-dispatch"
    assert options["agent"] == "orion"
    assert options["model"] == "openai/gpt-5.4-mini"
    assert options["tools"] == ["cortex"]
    assert options["_endpoint_request_id"]


@pytest.mark.asyncio
async def test_strict_persona_rejects_model(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_hydrate(agent: str, transcript_id: str | None) -> HydrationBundle:
        return _bundle(
            AgentMeta(
                default_model="xai/grok-4.20-multi-agent-0309",
                allowed_models=["xai/grok-4.20-multi-agent-0309"],
                tools=None,
                allowed_options=None,
            )
        )

    monkeypatch.setattr(
        "systems.frontier_consult.service.hydrate_agent",
        fake_hydrate,
    )

    req = FrontierGenerateRequest(
        messages=[{"role": "user", "content": "x"}],
        agent="oppie",
        model="openai/gpt-5.4",
    )
    with pytest.raises(FrontierEndpointError) as exc:
        await build_dispatch_body(req)
    assert exc.value.field == "model"


@pytest.mark.asyncio
async def test_strict_persona_rejects_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_hydrate(agent: str, transcript_id: str | None) -> HydrationBundle:
        return _bundle(
            AgentMeta(
                default_model="anthropic/claude-sonnet-4-6",
                allowed_models=["anthropic/claude-sonnet-4-6"],
                tools=["cortex"],
                allowed_options=None,
            )
        )

    monkeypatch.setattr(
        "systems.frontier_consult.service.hydrate_agent",
        fake_hydrate,
    )

    req = FrontierGenerateRequest(
        messages=[{"role": "user", "content": "x"}],
        agent="api-claude",
        tools=["agent_bus"],
    )
    with pytest.raises(FrontierEndpointError) as exc:
        await build_dispatch_body(req)
    assert exc.value.field == "tools"


@pytest.mark.asyncio
async def test_strict_persona_rejects_generation_options_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_hydrate(agent: str, transcript_id: str | None) -> HydrationBundle:
        return _bundle(
            AgentMeta(
                default_model="openai/gpt-5.4-mini",
                allowed_models=["openai/gpt-5.4-mini"],
                tools=None,
                allowed_options=["max_tokens"],
            )
        )

    monkeypatch.setattr(
        "systems.frontier_consult.service.hydrate_agent",
        fake_hydrate,
    )

    req = FrontierGenerateRequest(
        messages=[{"role": "user", "content": "x"}],
        agent="orion",
        generation_options={"temperature": 0.3},
    )
    with pytest.raises(FrontierEndpointError) as exc:
        await build_dispatch_body(req)
    assert exc.value.field == "generation_options"


@pytest.mark.asyncio
async def test_default_model_used_when_omitted(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_hydrate(agent: str, transcript_id: str | None) -> HydrationBundle:
        return _bundle(
            AgentMeta(
                default_model="openai/gpt-5.4-mini",
                allowed_models=["openai/gpt-5.4-mini"],
                tools=None,
                allowed_options=None,
            )
        )

    monkeypatch.setattr(
        "systems.frontier_consult.service.hydrate_agent",
        fake_hydrate,
    )

    req = FrontierGenerateRequest(
        messages=[{"role": "user", "content": "x"}], agent="orion"
    )
    body = await build_dispatch_body(req)
    assert body["pipeline_options"]["agent"] == "orion"
    assert body["pipeline_options"]["model"] == "openai/gpt-5.4-mini"


@pytest.mark.asyncio
async def test_request_id_is_propagated_and_permissive_tools_fallback() -> None:
    req = FrontierGenerateRequest(
        messages=[{"role": "user", "content": "x"}], model="openai/gpt-5.4-mini"
    )
    body = await build_dispatch_body(req)
    options: dict[str, Any] = body["pipeline_options"]
    assert options["_endpoint_request_id"]
    assert "tools" not in options
    assert options["mcp"] is True
