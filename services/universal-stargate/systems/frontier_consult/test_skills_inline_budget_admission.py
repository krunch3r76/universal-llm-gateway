"""Tests for caller-origin Layer-C inline budget hard-error admission (A1)."""

from __future__ import annotations

import re
from typing import Any

import pytest
from agent_seat import AgentMeta, HydrationBundle
from agent_seat.body_injection import INJECTED_BODY_BUDGET_BYTES, RequiredBodyUnresolved

from .events import DispatchSkillsInlineRejected
from .service import FrontierEndpointError, FrontierGenerateRequest, build_dispatch_body

_DISPATCH_THREAD = "test-dispatch-thread"
_SIGNAL_RE = re.compile(r"^[a-z]+(\.[a-z]+){1,4}$")


def _bundle(meta: AgentMeta | None = None) -> HydrationBundle:
    return HydrationBundle(
        briefing_card_md="# briefing",
        agent_meta=meta or AgentMeta(default_model="xai/grok-4.6"),
    )


def _layer_c_budget_drop(slug: str) -> list[dict[str, Any]]:
    return [
        {
            "id": f"agent_skill:{slug}",
            "reason": "layer_c_budget",
            "slug": slug,
        }
    ]


@pytest.mark.asyncio
async def test_role_path_caller_overflow_returns_skills_422(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_hydrate(
        agent: str, transcript_id: str | None = None, **_k: Any
    ) -> HydrationBundle:
        return HydrationBundle(
            briefing_card_md="# briefing",
            agent_meta=AgentMeta(default_model="xai/grok-4.6"),
            inline_only=True,
            required_body_unresolved=True,
            required_body_dropped=_layer_c_budget_drop("architecture-invariants"),
        )

    monkeypatch.setattr(
        "systems.frontier_consult.service.hydrate_agent",
        fake_hydrate,
    )

    events: list[Any] = []
    req = FrontierGenerateRequest(
        messages=[{"role": "user", "content": "x"}],
        role="grok-api-multi",
        dispatch_thread_id=_DISPATCH_THREAD,
        model="xai/grok-4.6",
        skills=["architecture-invariants"],
    )
    with pytest.raises(FrontierEndpointError) as exc_info:
        await build_dispatch_body(req, event_publisher=events.append)
    err = exc_info.value
    assert err.status_code == 422
    assert err.code == "skills_inline_budget_exceeded"
    assert err.field == "skills"
    assert err.details is not None
    assert err.details["skills"] == ["architecture-invariants"]
    assert err.details["budget_bytes"] == INJECTED_BODY_BUDGET_BYTES
    assert err.details["reason_code"] == "budget"
    rejected = [e for e in events if e.signal == "dispatch.skills.inline.rejected"]
    assert len(rejected) == 1
    payload = rejected[0].payload
    assert payload["skills"] == ["architecture-invariants"]
    assert payload["budget_bytes"] == INJECTED_BODY_BUDGET_BYTES
    assert payload["reason_code"] == "budget"


@pytest.mark.asyncio
async def test_role_path_coding_bundle_overflow_keeps_infra_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_hydrate(
        agent: str, transcript_id: str | None = None, **_k: Any
    ) -> HydrationBundle:
        return HydrationBundle(
            briefing_card_md="# briefing",
            agent_meta=AgentMeta(default_model="xai/grok-4.6"),
            inline_only=True,
            required_body_unresolved=True,
            required_body_dropped=[
                {
                    "id": "rule:architecture-invariants",
                    "reason": "layer_c_budget",
                    "slug": "architecture-invariants",
                }
            ],
        )

    monkeypatch.setattr(
        "systems.frontier_consult.service.hydrate_agent",
        fake_hydrate,
    )

    req = FrontierGenerateRequest(
        messages=[{"role": "user", "content": "x"}],
        role="grok-api-multi",
        dispatch_thread_id=_DISPATCH_THREAD,
        resolved_contract="implement",
    )
    with pytest.raises(FrontierEndpointError) as exc_info:
        await build_dispatch_body(req)
    assert exc_info.value.field == "injected_bodies"
    assert exc_info.value.code == "persona_violation"


@pytest.mark.asyncio
async def test_role_path_critical_drop_keeps_infra_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_hydrate(
        agent: str, transcript_id: str | None = None, **_k: Any
    ) -> HydrationBundle:
        return HydrationBundle(
            briefing_card_md="# briefing",
            agent_meta=AgentMeta(default_model="xai/grok-4.6"),
            inline_only=True,
            required_body_unresolved=True,
            required_body_dropped=[
                {"id": "rule:critical", "reason": "budget", "tier": "critical"}
            ],
        )

    monkeypatch.setattr(
        "systems.frontier_consult.service.hydrate_agent",
        fake_hydrate,
    )

    req = FrontierGenerateRequest(
        messages=[{"role": "user", "content": "x"}],
        role="grok-api-multi",
        dispatch_thread_id=_DISPATCH_THREAD,
        skills=["architecture-invariants"],
        model="xai/grok-4.6",
    )
    with pytest.raises(FrontierEndpointError) as exc_info:
        await build_dispatch_body(req)
    assert exc_info.value.field == "injected_bodies"


@pytest.mark.asyncio
async def test_role_less_path_caller_overflow_returns_422_not_500(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_resolve(*_a: Any, **_k: Any) -> Any:
        raise RequiredBodyUnresolved(_layer_c_budget_drop("architecture-invariants"))

    monkeypatch.setattr(
        "systems.frontier_consult.service.resolve_injected_bodies",
        fake_resolve,
    )

    events: list[Any] = []
    req = FrontierGenerateRequest(
        messages=[{"role": "user", "content": "x"}],
        model="xai/grok-4.6",
        skills=["architecture-invariants"],
    )
    with pytest.raises(FrontierEndpointError) as exc_info:
        await build_dispatch_body(req, event_publisher=events.append)
    err = exc_info.value
    assert err.status_code == 422
    assert err.code == "skills_inline_budget_exceeded"
    assert err.field == "skills"
    assert any(e.signal == "dispatch.skills.inline.rejected" for e in events)


@pytest.mark.asyncio
async def test_role_less_path_within_budget_ok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_seat.inject_registry import InjectResolution

    def fake_resolve(*_a: Any, **_k: Any) -> InjectResolution:
        return InjectResolution(
            block_md="<!-- injected -->",
            injected=[{"id": "agent_skill:architecture-invariants", "bytes": 10}],
            dropped=[],
            telemetry={},
        )

    monkeypatch.setattr(
        "systems.frontier_consult.service.resolve_injected_bodies",
        fake_resolve,
    )

    req = FrontierGenerateRequest(
        messages=[{"role": "user", "content": "x"}],
        model="xai/grok-4.6",
        skills=["architecture-invariants"],
    )
    body = await build_dispatch_body(req)
    assert "<!-- injected -->" in body["pipeline_options"]["system"]


@pytest.mark.asyncio
async def test_role_less_scope_default_overflow_soft_degrades(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_seat.inject_registry import InjectResolution

    def fake_resolve(*_a: Any, **_k: Any) -> InjectResolution:
        fail_closed = (
            "<!-- inject:FAIL_CLOSED entity_id=agent_skill:cortex-orientation "
            "reason=budget tier=must_inline -->"
        )
        return InjectResolution(
            block_md=fail_closed,
            injected=[],
            dropped=[
                {
                    "id": "agent_skill:cortex-orientation",
                    "reason": "budget_fail_closed",
                }
            ],
            telemetry={"fail_closed_reason": "must_inline_budget"},
        )

    monkeypatch.setattr(
        "systems.frontier_consult.service.resolve_injected_bodies",
        fake_resolve,
    )

    req = FrontierGenerateRequest(
        messages=[{"role": "user", "content": "x"}],
        model="xai/grok-4.6",
    )
    body = await build_dispatch_body(req)
    assert "inject:FAIL_CLOSED" in body["pipeline_options"]["system"]


def test_dispatch_skills_inline_rejected_event_catalog_signal() -> None:
    event = DispatchSkillsInlineRejected(
        request_id="req-test",
        role="reviewer",
        model="xai/grok-4.6",
        skills=["architecture-invariants"],
        budget_bytes=INJECTED_BODY_BUDGET_BYTES,
    )
    assert event.signal == "dispatch.skills.inline.rejected"
    assert _SIGNAL_RE.match(event.signal)
    assert set(event.payload) >= {
        "request_id",
        "role",
        "model",
        "skills",
        "budget_bytes",
        "reason_code",
    }
