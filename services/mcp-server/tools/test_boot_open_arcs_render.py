"""Tests for arc digest replacing ## Open arcs on the briefing card."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools._boot_helpers._briefing_card import render_briefing_card


def _sample_arc(*, n_children: int = 2) -> list[dict]:
    children = [
        {"id": f"todo:child-{i}", "workflow_state": "open"} for i in range(n_children)
    ]
    return [
        {
            "id": "task:x",
            "name": "X",
            "workflow_state": "in_progress",
            "children": children,
        }
    ]


def test_arc_section_includes_open_arc_summary() -> None:
    card, _manifest = render_briefing_card(open_arcs=_sample_arc())
    assert "## Arc — been → are → going" in card
    assert "`task:x` [in_progress](2)" in card
    assert "## Open arcs" not in card


def test_arc_section_omitted_when_no_arc_inputs() -> None:
    card, _ = render_briefing_card(open_arcs=[])
    assert "## Arc — been → are → going" not in card
    assert "## Open arcs" not in card


def test_todos_unchanged_when_arc_present() -> None:
    card, manifest = render_briefing_card(
        open_arcs=_sample_arc(),
        todos=[{"id": "todo:open", "title": "Open todo", "priority": "high"}],
        todo_total=1,
    )
    assert "## Todos — 1 open" in card
    recent = next(s for s in manifest if s.get("section") == "recent_work")
    assert recent["open_arcs"] == 1


def test_entity_hierarchy_orientation_block() -> None:
    card, _ = render_briefing_card()
    assert "## Entity granularity" in card
    assert "child_of" in card
    assert "Todos have steps; plans have phases" in card
    assert "PHASE 1/2/3" in card
