"""Tests for compact boot block render (fields 1+2)."""

from __future__ import annotations

import re
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools._boot_helpers._briefing_card import render_briefing_card
from tools._boot_helpers._briefing_card_render import render_compact_block
from tools._boot_helpers._orientation_blocks import render_orientation_blocks

_DENYLIST = re.compile(
    r"(you are |your role is|you, as the|frontier.independence|anti.corporate|<persona-token>)",
    re.IGNORECASE,
)

_NOW = datetime(2026, 6, 7, 12, 0, tzinfo=UTC)


def test_render_compact_block_field1_only_when_durable_identity_set() -> None:
    lines, ids = render_compact_block(
        principal_name="Kaywan Mansubi",
        durable_identity="Principal and final decision authority.",
        active_matters=[],
        today=_NOW,
    )
    text = "\n".join(lines)
    assert "## Compact" in text
    assert "**Kaywan Mansubi**" in text
    assert ids == set()


def test_render_compact_block_omits_field1_when_identity_unset() -> None:
    lines, _ids = render_compact_block(
        principal_name="Kaywan Mansubi",
        durable_identity=None,
        active_matters=[
            {
                "id": 99,
                "entity_name": "Estate Appeal",
                "claim": "Annual base year value appeal window open.",
                "valid_until": "2026-09-15T00:00:00Z",
            }
        ],
        today=_NOW,
    )
    text = "\n".join(lines)
    assert "Kaywan Mansubi" not in text
    assert "Estate Appeal" in text


def test_render_compact_block_denylist_clean() -> None:
    lines, _ = render_compact_block(
        principal_name="Kaywan Mansubi",
        durable_identity="AI operator — infrastructure orchestration.",
        active_matters=[
            {
                "id": 1,
                "entity_name": "Matter X",
                "claim": "Status update for active legal matter.",
                "valid_until": "2026-08-01T00:00:00Z",
            }
        ],
        today=_NOW,
    )
    assert _DENYLIST.search("\n".join(lines)) is None


def test_orientation_blocks_denylist_clean() -> None:
    blocks = render_orientation_blocks(family="claude", agent="claude-web")
    assert _DENYLIST.search("\n".join(blocks)) is None


def test_briefing_card_dedups_temporal_active_against_compact() -> None:
    shared_row = {
        "id": 42,
        "entity_name": "Shared Matter",
        "claim": "Active legal matter status.",
        "valid_until": "2026-08-01T00:00:00Z",
    }
    other_row = {
        "id": 43,
        "entity_name": "Other",
        "claim": "Unrelated temporal row.",
        "valid_until": "2026-09-01T00:00:00Z",
    }
    card, _manifest = render_briefing_card(
        temporal_active=[shared_row, other_row],
        principal_context={
            "principal_id": "person:kaywan-mansubi",
            "principal_name": "Kaywan Mansubi",
            "durable_identity": None,
            "active_matters": [shared_row],
        },
    )
    assert card.count("Shared Matter") == 1
    assert "Other" in card
    assert "## Temporally Active (1)" in card
