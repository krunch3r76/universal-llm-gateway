"""Bus endpoint address normalization — address layer (Phase 2 §B)."""

from __future__ import annotations

import pytest

from agent_seat.profiles import get_profile, load_profiles, seat_to_family
from agent_seat.registry import (
    _bus_address_map,
    expand_recipient_slugs,
    normalize_agent_slug,
    normalize_bus_address,
    resolve_agent_model,
    resolve_agent_provider,
    resolve_agent_valid_family,
    resolve_capability_cell_from_bus_address,
)


@pytest.mark.parametrize(
    ("slug", "expected"),
    [
        ("claude-web", "web-anthropic"),
        ("claude-api", "api-anthropic"),
        ("gpt-api", "api-openai"),
        ("grok-api", "api-xai"),
        ("grok-web", "web-xai"),
        ("gemini-api", "api-google"),
        ("gemini-web", "web-google"),
        ("claude-cursor", "cursor"),
        ("gpt-cursor", "cursor"),
        ("gemini-cursor", "cursor"),
        ("grok-cursor", "cursor"),
        ("web-anthropic", "web-anthropic"),
        ("api-openai", "api-openai"),
        ("cursor", "cursor"),
        ("web", "web-anthropic"),
        ("web_claude", "web-anthropic"),
        ("cdp", "web-anthropic"),  # retired bus seat → endpoint
    ],
)
def test_normalize_bus_address_exact(slug: str, expected: str) -> None:
    assert normalize_bus_address(slug) == expected
    assert normalize_bus_address(slug.replace("-", "_")) == expected


def test_capability_layer_frozen_equal_snapshot() -> None:
    """normalize_agent_slug + resolve_agent_* unchanged for dispatchable profile cells."""
    for family, platform in load_profiles():
        slug = f"{family}-{platform}"
        assert normalize_agent_slug(slug) == slug
        if platform in ("sdk", "subagent", "web"):
            continue
        profile = get_profile(family, platform)
        if not profile.default_model:
            continue
        resolve_agent_provider(slug)
        resolve_agent_model(slug)
        resolve_agent_valid_family(slug)


def test_bus_address_map_injective_outside_cursor_fold() -> None:
    addr_to_cells: dict[str, list[str]] = {}
    for cell, addr in _bus_address_map().items():
        addr_to_cells.setdefault(addr, []).append(cell)
    for addr, cells in addr_to_cells.items():
        if addr == "cursor":
            assert len(cells) >= 2
        else:
            assert len(cells) == 1


def test_seat_to_family_provider_scoped_and_cursor_guard() -> None:
    assert seat_to_family("web-anthropic") == "claude"
    assert seat_to_family("api-openai") == "gpt"
    assert seat_to_family("claude-cursor") == "claude"
    assert seat_to_family("cursor") is None


def test_expand_recipient_slugs_cursor_superset() -> None:
    expanded = set(expand_recipient_slugs("cursor"))
    assert expanded >= {
        "cursor",
        "claude-cursor",
        "gpt-cursor",
        "gemini-cursor",
        "grok-cursor",
    }


def test_expand_recipient_slugs_web_anthropic_superset() -> None:
    expanded = set(expand_recipient_slugs("web-anthropic"))
    assert expanded >= {"web-anthropic", "claude-web", "web", "web_claude"}


def test_resolve_capability_cell_web_anthropic() -> None:
    assert resolve_capability_cell_from_bus_address("web-anthropic") == ("claude", "web")


def test_resolve_capability_cell_cursor_defaults_claude() -> None:
    assert resolve_capability_cell_from_bus_address("cursor") == ("claude", "cursor")


def test_wait_round_trip_old_new() -> None:
    from agent_bus_store.wait_status import is_complete

    thread = {"status": "active"}
    comp_old = {"mode": "first_reply_from", "from_agent": "claude-web"}
    turns_new = [
        {"turn_number": 1, "from_agent": "dispatch"},
        {"turn_number": 2, "from_agent": "web-anthropic"},
    ]
    assert is_complete(thread, turns_new, after_turn=1, completion=comp_old)

    comp_new = {"mode": "first_reply_from", "from_agent": "web-anthropic"}
    turns_old = [
        {"turn_number": 1, "from_agent": "dispatch"},
        {"turn_number": 2, "from_agent": "claude-web"},
    ]
    assert is_complete(thread, turns_old, after_turn=1, completion=comp_new)


def test_disposition_equivalence_old_new() -> None:
    from agent_bus_store.disposition import agents_match

    assert agents_match("claude-web", "web-anthropic")
    assert agents_match("claude-cursor", "cursor")


@pytest.mark.parametrize(
    ("slug", "expected"),
    [
        ("cursor-auto", "cursor-auto"),
        ("cursor-monitor-6661", "cursor-monitor-6661"),
        ("grok-direct", "grok-direct"),
        ("CURSOR-SDK", "cursor-sdk"),
    ],
)
def test_unregistered_slug_preserves_hyphens(slug: str, expected: str) -> None:
    assert normalize_agent_slug(slug) == expected
    assert normalize_bus_address(slug) == expected
