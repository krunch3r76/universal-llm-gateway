from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools._boot_helpers._briefing_card import render_briefing_card


def _render(continuity: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    return render_briefing_card(
        last_session={
            "agent": "web",
            "timestamp": "2026-05-04T00:00:00Z",
            "summary": "Reviewed the handoff-capture arc.",
            "open_items": ["Finish the final pass."],
        },
        continuity=continuity,
    )


def test_render_last_session_with_handoff_and_continuity_block() -> None:
    card, manifest = _render(
        {
            "handoff": {
                "entry_id": 42,
                "text": "Start with the tests, then confirm the OpenAPI surface.",
            },
            "continuity_chain": ["web-2026-05-03-1845", "web-2026-05-04-0049"],
            "continuations": [],
            "hints": [],
        }
    )

    assert "**Handoff**" in card
    assert "Start with the tests, then confirm the OpenAPI surface." in card
    assert "**Continuity**" in card
    assert "web-2026-05-03-1845 → web-2026-05-04-0049 → [you are here]" in card
    assert {
        "section": "continuity",
        "has_handoff": True,
        "hint": "GET /boot-continuity via cortex-api",
    } in manifest


def test_render_last_session_without_handoff_uses_hint_and_manifest_flag() -> None:
    card, manifest = _render(
        {
            "handoff": None,
            "continuity_chain": ["web-2026-05-03-1845", "web-2026-05-04-0049"],
            "continuations": [],
            "hints": ["no_handoff_captured"],
        }
    )

    assert "Reviewed the handoff-capture arc." in card
    assert "_Hint: no_handoff_captured_" in card
    assert "**Continuity**" in card
    assert {
        "section": "continuity",
        "has_handoff": False,
        "hint": "GET /boot-continuity via cortex-api",
    } in manifest


def test_render_multi_continuation_siblings() -> None:
    card, _manifest = _render(
        {
            "handoff": {"entry_id": 9, "text": "Merge the final pass carefully."},
            "continuity_chain": ["web-2026-05-01-1845", "web-2026-05-03-2351"],
            "continuations": ["web-2026-05-02-0900"],
            "hints": [],
        }
    )

    assert (
        "web-2026-05-01-1845 → [continuations: web-2026-05-02-0900, web-2026-05-03-2351] → [you are here]"
        in card
    )
