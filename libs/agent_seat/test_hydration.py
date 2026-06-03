"""Tests for async Cortex hydration.

Covers parallel fetch orchestration + briefing rendering + graceful fallback
when individual fetches error. Network I/O is replaced by scripted stubs
that assert each expected endpoint is requested.
"""

from __future__ import annotations

from typing import Any

import pytest

from agent_seat import hydration as _hyd
from agent_seat.hydration import HydrationBundle, hydrate_agent


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
        # gemini is policy inline-only on the (gemini, api) profile, so an
        # explicit model=gemini override on any write-capable role is suppressed.
        ("google/gemini-3.5-flash", True),
        ("google/gemini-2.5-pro", True),
        # Non-inline-only families are unaffected.
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
async def test_hydrate_reviewer_with_explicit_gemini_is_inline_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """reviewer (default gpt, write-capable) + explicit model=gemini must be
    coerced to capability_tier=inline-only — capability binds to the effective
    model, not the role label (Guard 1 anti-corruption)."""
    monkeypatch.setattr(_hyd, "_cortex_get", _Scripted({"/": {}}))
    monkeypatch.setattr(_hyd, "_bus_get", _Scripted({}))

    bundle = await hydrate_agent("reviewer", model="google/gemini-2.5-pro")

    assert bundle.inline_only is True
    assert bundle.agent_meta.capability_tier == "inline-only"


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
async def test_hydrate_synthesizer_inherits_gemini_api_inline_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """synthesizer → gemini/api capability_tier=inline-only suppresses MCP writes."""
    monkeypatch.setattr(_hyd, "_cortex_get", _Scripted({"/": {}}))
    monkeypatch.setattr(_hyd, "_bus_get", _Scripted({}))

    bundle = await hydrate_agent("synthesizer")

    assert bundle.inline_only is True
    assert bundle.agent_meta.capability_tier == "inline-only"
