"""Priority continuity hub card templates."""

from __future__ import annotations

import pytest

from agent_bus_store.continuity_hub_cards import (
    PRIORITY_HUB_CARDS,
    entity_create_payload,
    hub_card_uri,
    render_priority_card,
)

pytestmark = pytest.mark.offline


@pytest.mark.parametrize("root", ["9740", "10327"])
def test_render_priority_card_has_required_headings(root):
    body = render_priority_card(root)
    assert body is not None
    for heading in (
        "## Skills",
        "## Stance",
        "## Why this house",
        "## Objective",
        "## Runbooks",
        "## Rules",
        "## Sidecars",
        "## Scratchboards",
        "## House",
    ):
        assert heading in body
    assert "`ulg-for-llms`" in body
    assert f"document:{root}-continuity" in body


def test_entity_create_payload_matches_card():
    payload = entity_create_payload("9740", card_sha256="sha256:abc")
    assert payload["id"] == "document:9740-continuity"
    assert payload["content_hash"] == "sha256:abc"
    assert "PERPS_TRADER_FIRE" in PRIORITY_HUB_CARDS["9740"]["objective"]


def test_hub_card_uri_uses_card_md_only_house(
    monkeypatch: pytest.MonkeyPatch, tmp_path
):
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    card = tmp_path / "notes/system/threads/10479-card.md"
    card.parent.mkdir(parents=True)
    card.write_text("# live\n", encoding="utf-8")
    assert hub_card_uri("10479") == "cortex://notes/system/threads/10479-card.md"


def test_hub_card_uri_uses_continuity_card_only_house(
    monkeypatch: pytest.MonkeyPatch, tmp_path
):
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    card = tmp_path / "notes/system/threads/10479-continuity-card.md"
    card.parent.mkdir(parents=True)
    card.write_text("# continuity card\n", encoding="utf-8")
    assert (
        hub_card_uri("10479")
        == "cortex://notes/system/threads/10479-continuity-card.md"
    )
