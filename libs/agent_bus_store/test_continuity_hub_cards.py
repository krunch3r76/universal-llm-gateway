"""Priority continuity hub card templates."""

from __future__ import annotations

import pytest

from agent_bus_store.continuity_hub_cards import (
    PRIORITY_HUB_CARDS,
    entity_create_payload,
    render_priority_card,
)

pytestmark = pytest.mark.offline


@pytest.mark.parametrize("root", ["9740", "10327"])
def test_render_priority_card_has_required_headings(root):
    body = render_priority_card(root)
    assert body is not None
    for heading in (
        "## Stance",
        "## Why this house",
        "## Objective",
        "## Runbooks",
        "## House",
    ):
        assert heading in body
    assert f"document:{root}-continuity" in body


def test_entity_create_payload_matches_card():
    payload = entity_create_payload("9740", card_sha256="sha256:abc")
    assert payload["id"] == "document:9740-continuity"
    assert payload["content_hash"] == "sha256:abc"
    assert "PERPS_TRADER_FIRE" in PRIORITY_HUB_CARDS["9740"]["objective"]
