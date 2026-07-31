"""Arc digest renderer — thread 1427 (directive 3 / assertion 13717)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datetime import UTC, datetime

from tools._boot_helpers._briefing_card import render_briefing_card
from tools._boot_helpers._briefing_card_render import render_arc_section

_NOW = datetime(2026, 6, 10, 3, 0, tzinfo=UTC)


def _full_inputs():
    return {
        "continuity": {"continuity_chain": ["a-1", "a-2", "a-3", "a-4"]},
        "last_session": {
            "agent": "claude-web",
            "timestamp": "2026-06-10T02:00:00Z",
            "summary": "Did a thing. " * 40,
            "open_items": ["item one", "item two", "item three"],
        },
        "open_arcs": [
            {
                "id": "task:x",
                "workflow_state": "in_progress",
                "children": [{"id": "todo:a"}],
            }
        ],
        "in_flight_todos": [{"id": "todo:b"}],
        "deadlines": [
            {"deadline_date": "2026-08", "deadline_name": "unparseable-month"},
            {"deadline_date": "2026-09-15", "deadline_name": "Appeal Window"},
        ],
        "now": _NOW,
    }


def test_renders_triad_with_absorbed_content() -> None:
    out = "\n".join(render_arc_section(**_full_inputs()))
    assert "## Arc — been → are → going" in out
    assert "**Been**" in out and "a-2 → a-3 → a-4 → here" in out
    assert "**Are**" in out and "`task:x` [in_progress](1)" in out
    assert "in-flight: `todo:b`" in out
    assert "**Going**" in out and "item one; item two (+1 more)" in out


def test_summary_truncation_carries_recovery_handle() -> None:
    out = "\n".join(render_arc_section(**_full_inputs()))
    assert "journal_read" in out and "ch —" in out


def test_nearest_deadline_skips_unparseable_dates() -> None:
    out = "\n".join(render_arc_section(**_full_inputs()))
    assert "2026-09-15" in out and "Appeal Window" in out
    assert "unparseable-month" not in out


def test_empty_inputs_render_nothing() -> None:
    assert (
        render_arc_section(
            continuity=None,
            last_session=None,
            open_arcs=None,
            in_flight_todos=None,
            deadlines=None,
            now=_NOW,
        )
        == []
    )


def test_briefing_card_arc_replaces_open_arcs_and_last_session() -> None:
    card, _manifest = render_briefing_card(
        last_session={
            "agent": "claude-cursor",
            "timestamp": "2026-06-10T02:00:00Z",
            "summary": "Planning arc complete.",
            "open_items": ["Follow up on thread 1427"],
        },
        continuity={"continuity_chain": ["s-1", "s-2"]},
        open_arcs=[
            {
                "id": "task:boot-arc",
                "workflow_state": "in_progress",
                "children": [{"id": "todo:impl"}],
            }
        ],
    )
    assert "## Arc — been → are → going" in card
    assert "## Last Session" not in card
    assert "## Open arcs" not in card
    assert "## Recent Work" not in card


def test_your_notes_truncated_claim_carries_handle() -> None:
    long_claim = "x" * 1000
    recent = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    card, _ = render_briefing_card(
        self_reflections=[
            {
                "id": 13717,
                "claim": long_claim,
                "session_tag": "web-2026-06-10-0107",
                "created_at": recent,
            }
        ],
        family="claude",
    )
    assert "[a13717 +" in card
    assert "full text by id" in card


def test_your_notes_short_claim_no_handle() -> None:
    recent = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    card, _ = render_briefing_card(
        self_reflections=[
            {
                "id": 99,
                "claim": "Short directive.",
                "session_tag": "web-2026-06-10-0107",
                "created_at": recent,
            }
        ],
        family="claude",
    )
    assert "[a99 +" not in card
    assert "Short directive." in card


def test_your_notes_drops_identity_override_claims() -> None:
    recent = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    card, _ = render_briefing_card(
        self_reflections=[
            {
                "id": 1,
                "claim": (
                    "Operator naming + duty (standing): agent addressable name "
                    "is Max; Kaywan navigates."
                ),
                "session_tag": "web-2026-07-11-1624",
                "created_at": recent,
            },
            {
                "id": 2,
                "claim": "Prefer pointer-first corpus on code seats.",
                "session_tag": "web-2026-07-18-1000",
                "created_at": recent,
            },
        ],
        family="claude",
    )
    assert "addressable name" not in card
    assert "Prefer pointer-first corpus" in card
