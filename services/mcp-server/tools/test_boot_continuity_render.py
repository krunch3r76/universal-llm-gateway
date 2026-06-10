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


def test_render_last_session_with_handoff_shows_summary_not_prose() -> None:
    """Per assertion 8384: handoff prose MUST NOT auto-surface on the boot card.

    Handoffs are user-facing artifacts for manual copy-paste at end of chat.
    Even when a handoff is captured for the last session, the boot card
    renders only the session summary; the continuity chain still renders.
    """
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

    assert "**Handoff**" not in card
    assert "Start with the tests, then confirm the OpenAPI surface." not in card
    assert "## Arc — been → are → going" in card
    assert "Reviewed the handoff-capture arc." in card
    assert "web-2026-05-03-1845 → web-2026-05-04-0049 → here" in card
    assert "## Last Session" not in card
    assert {
        "section": "continuity",
        "hint": "GET /boot-continuity via cortex-api",
    } in manifest


def test_render_last_session_without_handoff_no_hint() -> None:
    """Absence of a handoff is not a gap — `no_handoff_captured` hint retired."""
    card, manifest = _render(
        {
            "handoff": None,
            "continuity_chain": ["web-2026-05-03-1845", "web-2026-05-04-0049"],
            "continuations": [],
            "hints": [],
        }
    )

    assert "Reviewed the handoff-capture arc." in card
    assert "_Hint: no_handoff_captured_" not in card
    assert "**Handoff**" not in card
    assert "## Arc — been → are → going" in card
    assert {
        "section": "continuity",
        "hint": "GET /boot-continuity via cortex-api",
    } in manifest


def test_resuming_from_unverified_handoff_surfaces_flag_not_prose() -> None:
    """Resuming from a detached_string handoff surfaces a flag-only caution.

    The flag + derivation + standing re-derive rule appear so the agent gets a
    boot-time signal; the handoff prose itself stays suppressed (decision 8384).
    """
    card, _manifest = render_briefing_card(
        transcript_continuation={
            "entity_id": "transcript:claude-web-2026-06-04-2112",
            "summary": "VRAM-accounting arc continuity.",
            "handoff_surface": {
                "surfaced": True,
                "verified": False,
                "derivation": "detached_string",
                "flag": "unverified",
                "reason": "handoff_provenance.source_file is null.",
            },
        },
    )

    assert "## Resuming From: `transcript:claude-web-2026-06-04-2112`" in card
    assert "**Handoff UNVERIFIED**" in card
    assert "derivation=detached_string" in card
    assert "re-derive from source before acting" in card
    assert "decision 8384" in card
    # Prose / reason text MUST NOT inline.
    assert "handoff_provenance.source_file is null." not in card


def test_resuming_from_verified_handoff_no_warning() -> None:
    """A verified (file-backed) handoff surfaces no caution line."""
    card, _manifest = render_briefing_card(
        transcript_continuation={
            "entity_id": "transcript:claude-web-2026-06-04-2200",
            "summary": "Clean continuity.",
            "handoff_surface": {
                "surfaced": True,
                "verified": True,
                "derivation": "section",
            },
        },
    )

    assert "## Resuming From:" in card
    assert "UNVERIFIED" not in card
    assert "re-derive from source" not in card


def test_resuming_from_no_handoff_surface_no_warning() -> None:
    """Continuation without a handoff_surface (no handoff prompt) → no caution."""
    card, _manifest = render_briefing_card(
        transcript_continuation={
            "entity_id": "transcript:claude-web-2026-06-04-2300",
            "summary": "No handoff on this transcript.",
        },
    )

    assert "## Resuming From:" in card
    assert "Handoff" not in card


def test_render_multi_continuation_siblings_still_renders_chain() -> None:
    """Sibling-continuation chain rendering is unaffected by handoff retirement."""
    card, _manifest = _render(
        {
            "handoff": {"entry_id": 9, "text": "Merge the final pass carefully."},
            "continuity_chain": ["web-2026-05-01-1845", "web-2026-05-03-2351"],
            "continuations": ["web-2026-05-02-0900"],
            "hints": [],
        }
    )

    assert "**Handoff**" not in card
    assert "Merge the final pass carefully." not in card
    assert "## Arc — been → are → going" in card
    assert "web-2026-05-01-1845" in card
    assert "(+1 continuation(s))" in card
