"""Tests for ## Open arcs briefing-card render."""

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


def test_open_arcs_section_renders_when_non_empty() -> None:
    card, _manifest = render_briefing_card(open_arcs=_sample_arc())
    assert "## Open arcs" in card
    assert "`task:x`" in card
    assert "child-0" in card
    assert "child-1" in card
    assert "2 leaf todos" in card


def test_open_arcs_section_omitted_when_empty() -> None:
    card, _ = render_briefing_card(open_arcs=[])
    assert "## Open arcs" not in card


def test_open_arcs_child_cap_eight_plus_more() -> None:
    card, _ = render_briefing_card(open_arcs=_sample_arc(n_children=10))
    assert "+2 more" in card
    assert "child-7" in card
    assert "child-9" not in card


def test_recent_work_and_todos_unchanged_additive() -> None:
    card, manifest = render_briefing_card(
        open_arcs=_sample_arc(),
        plan_phases=[{"id": "plan_phase:p1", "name": "P1", "workflow_state": "done"}],
        in_flight_todos=[{"id": "todo:fly", "name": "Fly", "domain": "cortex"}],
        todos=[{"id": "todo:open", "title": "Open todo", "priority": "high"}],
        todo_total=1,
    )
    assert "## Recent Work" in card
    assert "## Todos — 1 open" in card
    assert "plan_phase:p1" in card
    assert "todo:fly" in card
    recent = next(s for s in manifest if s.get("section") == "recent_work")
    assert recent["open_arcs"] == 1


def test_entity_hierarchy_orientation_block() -> None:
    card, _ = render_briefing_card()
    assert "## Entity granularity" in card
    assert "child_of" in card
