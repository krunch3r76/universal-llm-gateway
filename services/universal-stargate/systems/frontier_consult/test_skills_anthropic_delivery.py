"""Anthropic skills= delivery — inline overflow entity pointers (ruling leg B)."""

from __future__ import annotations

from typing import Any

import pytest
from agent_seat import AgentMeta, HydrationBundle
from agent_seat.skills_merge import SkillChannelRow, SkillPartition

from .service import FrontierGenerateRequest, build_dispatch_body

_DISPATCH_THREAD = "test-anthropic-skills-delivery"


def _layer_c_budget_drop(slug: str) -> list[dict[str, Any]]:
    return [
        {
            "id": f"agent_skill:{slug}",
            "reason": "layer_c_budget",
            "slug": slug,
        }
    ]


@pytest.mark.asyncio
async def test_anthropic_role_path_caller_budget_entity_pointer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_hydrate(
        agent: str, transcript_id: str | None = None, **_k: Any
    ) -> HydrationBundle:
        return HydrationBundle(
            briefing_card_md="# briefing",
            agent_meta=AgentMeta(
                default_model="anthropic/claude-sonnet-5-5",
                allowed_models=["anthropic/claude-sonnet-5-5"],
            ),
            inline_only=True,
            required_body_unresolved=True,
            required_body_dropped=_layer_c_budget_drop("architecture-invariants"),
        )

    partition = SkillPartition(
        layer_b=(),
        layer_a=(),
        layer_c=("architecture-invariants",),
        provider_mount_slugs=frozenset(),
        rows=(
            SkillChannelRow(
                requested_id="architecture-invariants",
                canonical_id="architecture-invariants",
                origin="caller",
                channel="layer_c",
                disposition="delivered",
            ),
        ),
    )

    monkeypatch.setattr(
        "systems.frontier_consult.service.hydrate_agent",
        fake_hydrate,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.service.partition_skill_channels",
        lambda *_a, **_k: partition,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.service.caller_skill_ids_for_layer_c",
        lambda *_a, **_k: ("architecture-invariants",),
    )

    events: list[Any] = []
    req = FrontierGenerateRequest(
        messages=[{"role": "user", "content": "x"}],
        role="reviewer",
        dispatch_thread_id=_DISPATCH_THREAD,
        model="anthropic/claude-sonnet-5-5",
        skills=["architecture-invariants"],
    )
    body = await build_dispatch_body(req, event_publisher=events.append)
    system = body["pipeline_options"]["system"]
    assert "injected-index:architecture-invariants" in system
    assert any(e.signal == "dispatch.skills.inline.overflow" for e in events)
