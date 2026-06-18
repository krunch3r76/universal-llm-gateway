"""Tests for async Cortex hydration.

Covers parallel fetch orchestration + briefing rendering + graceful fallback
when individual fetches error. Network I/O is replaced by scripted stubs
that assert each expected endpoint is requested.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from agent_seat import hydration as _hyd
from agent_seat.hydration import AgentMeta, HydrationBundle, hydrate_agent


class _Scripted:
    """Fake HTTP responder keyed by path substring."""

    def __init__(self, mapping: dict[str, Any]) -> None:
        self.mapping = mapping
        self.calls: list[str] = []

    async def __call__(self, path: str) -> Any:
        self.calls.append(path)
        for fragment, response in self.mapping.items():
            if fragment in path:
                return response
        return {"error": f"no fake for {path}"}


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        # gemini/api is MCP-enabled (capability_tier removed); inline_only_for_model
        # must return False for all google models.
        ("google/gemini-3.5-flash", False),
        ("google/gemini-2.5-pro", False),
        # Other families unaffected.
        ("openai/gpt-5.5", False),
        ("anthropic/claude-opus-4-8", False),
        ("xai/grok-4.3", False),
    ],
)
def test_inline_only_for_model_binds_to_effective_family(
    model: str, expected: bool
) -> None:
    from agent_seat.profiles import inline_only_for_model

    assert inline_only_for_model(model) is expected


@pytest.mark.asyncio
async def test_hydrate_reviewer_with_explicit_gemini_is_mcp_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """reviewer + explicit model=gemini is MCP-enabled; gemini/api capability_tier
    is no longer inline-only so no coercion occurs."""
    monkeypatch.setattr(_hyd, "_cortex_get", _Scripted({"/": {}}))
    monkeypatch.setattr(_hyd, "_bus_get", _Scripted({}))

    bundle = await hydrate_agent("reviewer", model="google/gemini-2.5-pro")

    assert bundle.inline_only is False
    assert bundle.agent_meta.capability_tier != "inline-only"


@pytest.mark.asyncio
async def test_hydrate_agent_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    cortex_fake = _Scripted(
        {
            "/session-journals": [
                {
                    "agent": "gatherer",
                    "summary": "stale journal open items",
                    "timestamp": "2026-04-19T12:00:00Z",
                    "open_items": ["verify X", "ship Y"],
                }
            ],
            "/boot-continuity": {
                "last_session": {
                    "agent": "gatherer",
                    "summary": "last session was productive",
                    "timestamp": "2026-04-19T12:00:00Z",
                    "open_items": ["reconciled-only"],
                },
            },
            "/deadlines": [{"deadline_date": "2026-04-25", "deadline_name": "Filing"}],
            "/staging": [{"id": 1}, {"id": 2}],
            "/boot-todos": [
                {"id": "todo:foo", "priority": "high", "title": "ship"},
                {"id": "todo:bar", "priority": "medium", "title": "review"},
            ],
            "/assertions": [{"claim": "I tend to over-engineer"}],
        }
    )
    bus_fake = _Scripted(
        {
            "/threads?status=active": {
                "threads": [{"id": 1, "slug": "session-close", "unread_count": 2}]
            },
            "/turns": {"turns": [{"id": 99, "body": "unread message"}]},
        }
    )
    monkeypatch.setattr(_hyd, "_cortex_get", cortex_fake)
    monkeypatch.setattr(_hyd, "_bus_get", bus_fake)

    bundle = await hydrate_agent("gatherer")
    assert isinstance(bundle, HydrationBundle)
    assert bundle.continuation_id is None
    assert bundle.continuation_md is None

    # Briefing card contains key sections.
    card = bundle.briefing_card_md
    assert "Boot Briefing — gatherer" in card
    assert "Deadlines" in card and "Filing" in card
    assert "Agent Bus" in card
    assert "Review Queue" in card
    assert "Last Session" in card and "productive" in card
    assert "reconciled-only" in card
    assert "verify X" not in card
    assert "Todos" in card and "todo:foo" in card
    assert "Your Notes" in card

    assert bundle.section_counts["todos"] == 2
    assert bundle.section_counts["unread_turns"] == 1
    assert bundle.section_counts["deadlines"] == 1
    assert bundle.section_counts["briefing_bytes"] > 0

    # Parallel fetch actually called the expected endpoints.
    assert any("session-journals" in c for c in cortex_fake.calls)
    assert any("boot-continuity" in c for c in cortex_fake.calls)
    assert any("boot-todos" in c for c in cortex_fake.calls)
    assert any("threads" in c for c in bus_fake.calls)


@pytest.mark.asyncio
async def test_hydrate_agent_tolerates_endpoint_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error_everywhere = _Scripted({"/": {"error": "service down"}})
    monkeypatch.setattr(_hyd, "_cortex_get", error_everywhere)
    monkeypatch.setattr(_hyd, "_bus_get", error_everywhere)

    bundle = await hydrate_agent("skeptic")
    # Briefing still renders, just with empty sections.
    assert "Boot Briefing — skeptic" in bundle.briefing_card_md
    assert bundle.section_counts["todos"] == 0
    assert bundle.section_counts["deadlines"] == 0


@pytest.mark.asyncio
async def test_hydrate_agent_with_transcript_continuation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_cortex_get(path: str) -> Any:
        if "/entities/transcript:" in path:
            return {
                "name": "cursor-2026-04-19-2100",
                "description": "session on agent seats",
                "assertions": [],
            }
        return {}

    monkeypatch.setattr(_hyd, "_cortex_get", fake_cortex_get)
    monkeypatch.setattr(_hyd, "_bus_get", _Scripted({}))

    bundle = await hydrate_agent("web", transcript_id="cursor-2026-04-19-2100")
    assert bundle.continuation_id == "transcript:cursor-2026-04-19-2100"
    assert bundle.continuation_md is not None
    assert "session on agent seats" in bundle.continuation_md


@pytest.mark.asyncio
async def test_hydrate_agent_missing_transcript_logs_and_continues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_cortex_get(path: str) -> Any:
        if "/entities/transcript:" in path:
            return {"error": "not found"}
        return {}

    monkeypatch.setattr(_hyd, "_cortex_get", fake_cortex_get)
    monkeypatch.setattr(_hyd, "_bus_get", _Scripted({}))

    bundle = await hydrate_agent("web", transcript_id="missing")
    assert bundle.continuation_id is None
    assert bundle.continuation_md is None
    # Briefing still produced.
    assert bundle.briefing_card_md


@pytest.mark.asyncio
async def test_hydrate_synthesizer_is_mcp_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """synthesizer → gemini/api is MCP-enabled; capability_tier no longer
    inline-only so the tool surface is admitted."""
    monkeypatch.setattr(_hyd, "_cortex_get", _Scripted({"/": {}}))
    monkeypatch.setattr(_hyd, "_bus_get", _Scripted({}))

    bundle = await hydrate_agent("synthesizer")

    assert bundle.inline_only is False
    assert bundle.agent_meta.capability_tier != "inline-only"


@pytest.mark.asyncio
async def test_hydrate_role_loads_default_model_from_role_anchor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """team_dispatch roles must resolve default_model from role:*, not family:*."""

    class _RoleAwareScripted:
        calls: list[str] = []

        async def __call__(self, path: str) -> Any:
            _RoleAwareScripted.calls.append(path)
            if "role:synthesizer" in path:
                return {
                    "attributes": {
                        "default_model": "google/gemini-3.1-pro-preview",
                        "allowed_models": [
                            "google/gemini-3.1-pro-preview",
                            "google/gemini-3.5-flash",
                            "google/gemini-2.5-flash",
                            "google/gemini-2.5-pro",
                        ],
                        "frontier_kind": "google",
                    }
                }
            if "family:gemini" in path:
                return {"attributes": {}}
            return {}

    fake = _RoleAwareScripted()
    monkeypatch.setattr(_hyd, "_cortex_get", fake)
    monkeypatch.setattr(_hyd, "_bus_get", _Scripted({"/": {}}))

    bundle = await hydrate_agent("synthesizer")

    assert bundle.agent_meta.default_model == "google/gemini-3.1-pro-preview"
    assert "google/gemini-3.1-pro-preview" in bundle.agent_meta.allowed_models
    meta_entity_calls = [c for c in fake.calls if "/entities/" in c]
    assert any("role:synthesizer" in c for c in meta_entity_calls)
    assert not any("family:gemini" in c and "role:" not in c for c in meta_entity_calls)


@pytest.mark.asyncio
async def test_non_inline_seat_still_injects_registry_bodies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolve_calls: list[str] = []

    def fake_resolve(seat: str, **kwargs: object) -> MagicMock:
        resolve_calls.append(seat)
        resolution = MagicMock()
        resolution.block_md = "<!-- invariant-skill:foo digest:sha256:abc -->"
        resolution.injected = [{"id": "agent_skill:foo", "bytes": 10}]
        resolution.dropped = []
        resolution.telemetry = {"cold_fetches": 1}
        return resolution

    monkeypatch.setattr(_hyd, "_cortex_get", _Scripted({"/": {}}))
    monkeypatch.setattr(_hyd, "_bus_get", _Scripted({"/": {}}))
    monkeypatch.setattr(_hyd, "resolve_injected_bodies", fake_resolve)

    bundle = await hydrate_agent(
        "gatherer",
        model="openai/gpt-5.5",
        inject_profile="dispatch",
        packet_invariant_ids=("agent_skill:sentinel-dispatch-inject-19887",),
    )

    assert bundle.inline_only is False
    assert bundle.injected_bodies_md is not None
    assert resolve_calls == ["gatherer"]


@pytest.mark.asyncio
async def test_already_present_includes_continuation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, str] = {}

    def fake_resolve(seat: str, *, already_present: str = "", **_: object) -> MagicMock:
        seen["already_present"] = already_present
        resolution = MagicMock()
        resolution.block_md = ""
        resolution.injected = []
        resolution.dropped = []
        resolution.telemetry = {
            "elapsed_ms": 0,
            "cold_fetches": 0,
            "cache_hit": False,
            "deadline_hit": False,
        }
        return resolution

    monkeypatch.setattr(_hyd, "_cortex_get", _Scripted({"/": {}}))
    monkeypatch.setattr(_hyd, "_bus_get", _Scripted({"/": {}}))
    monkeypatch.setattr(_hyd, "resolve_injected_bodies", fake_resolve)

    async def fake_meta(_agent: str) -> AgentMeta:
        return AgentMeta(
            default_model="xai/grok-4.3-multi-agent",
            frontier_kind="xai",
            allowed_models=["xai/grok-4.3-multi-agent"],
        )

    async def fake_continuation(
        _transcript_id: str,
    ) -> tuple[str | None, str | None]:
        return (
            "## Resuming From: `transcript:abc123`\n**Summary**: prior\n",
            "transcript:abc123",
        )

    monkeypatch.setattr(_hyd, "_fetch_agent_meta", fake_meta)
    monkeypatch.setattr(_hyd, "_resolve_continuation", fake_continuation)

    bundle = await hydrate_agent(
        "grok-api-multi",
        transcript_id="abc123",
        model="xai/grok-4.3-multi-agent",
    )

    assert bundle.inline_only is True
    assert "Resuming From" in seen.get("already_present", "")
    assert bundle.briefing_card_md.split("Resuming From")[0] in seen["already_present"]


def test_static_tool_fallback_unique_names() -> None:
    """STATIC_TOOL_FALLBACK must have unique function.name values and exactly
    one 'cortex' entry — guards against the duplicate-cortex regression."""
    from agent_seat import STATIC_TOOL_FALLBACK

    names = [d.get("function", {}).get("name", "") for d in STATIC_TOOL_FALLBACK]
    assert len(names) == len(set(names)), f"Duplicate tool names: {names}"
    assert names.count("cortex") == 1, f"Expected exactly one cortex, got: {names}"
