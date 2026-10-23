"""Tests for maestro journal → hop ``## Lessons in force`` projection."""

from __future__ import annotations

import pytest

from claude_bundles.journal_hop_projection import (
    JOURNAL_HEAD_BYTES,
    hop_journal_projection_fields,
    project_lessons_in_force,
)
from claude_bundles.operator_proxy_standing_render import HOP_LESSONS_CAP_B

pytestmark = pytest.mark.offline


def test_lessons_in_force_entry_heading_matches_journal_h2() -> None:
    """Hop body must repeat journal entry headings at ## so induction cites match."""
    journal = "## 2026-10-20 — entry 333\nRULE: [route] cite\n"
    out = project_lessons_in_force(journal, role="maestro")
    heading = "## 2026-10-20 — entry 333"
    assert heading in out
    assert "### 2026-10-20 — entry 333" not in out
    lessons_block = out.split("## Lessons in force", 1)[1]
    assert "RULE: [route] cite" in lessons_block
    assert lessons_block.index(heading) < lessons_block.index("RULE: [route] cite")


def test_project_lessons_empty_journal_returns_none() -> None:
    assert (
        project_lessons_in_force("", role="maestro")
        == "## Lessons in force\n\n(none)\n"
    )
    assert (
        project_lessons_in_force("   \n", role="maestro")
        == "## Lessons in force\n\n(none)\n"
    )


def test_hop_projection_unreadable_journal_returns_unavailable() -> None:
    def _read_head(_uri: str, _limit: int) -> tuple[str | None, int | None]:
        raise OSError("disk")

    fields = hop_journal_projection_fields(read_journal_head=_read_head)
    assert fields["lessons_in_force"] == "## Lessons in force\n\njournal unavailable\n"


def test_hop_lessons_prefers_newest_lane_entry_over_file_order() -> None:
    """File order fat entry 341 then short 348 must project 348 first (trim may drop 341)."""
    heading_341 = "2026-10-20 — entry 341 — lane 12286"
    heading_348 = "2026-10-20 — entry 348 — lane 12286"
    pad = "z" * 1050
    journal = (
        f"## {heading_341}\nRULE: [route] payload-{pad}\n"
        f"## {heading_348}\nRULE: [route] short-newest\n"
    )
    lessons = hop_journal_projection_fields(journal_text=journal, lane="12286")[
        "lessons_in_force"
    ]
    assert "(none)" not in lessons
    assert "entry 348" in lessons
    if "entry 341" in lessons:
        assert lessons.index("entry 348") < lessons.index("entry 341")


def test_hop_lessons_keeps_single_entry_over_byte_cap() -> None:
    """Newest hop-lane entry stays whole even when the rendered block exceeds HOP_LESSONS_CAP_B."""
    heading = "2026-10-20 — entry 400 — lane 12286"
    pad = "x" * (HOP_LESSONS_CAP_B + 200)
    journal = f"## {heading}\nRULE: [route] bulk-{pad}\n"
    lessons = hop_journal_projection_fields(journal_text=journal, lane="12286")[
        "lessons_in_force"
    ]
    assert "(none)" not in lessons
    assert "entry 400" in lessons
    assert len(lessons.encode("utf-8")) > HOP_LESSONS_CAP_B


def _over_cap_lane_12286_journal() -> str:
    """Newest hop-lane entry renders over HOP_LESSONS_CAP_B; journal head over JOURNAL_HEAD_BYTES."""
    heading = "2026-10-20 — entry 341 — lane 12286"
    pad = "x" * (HOP_LESSONS_CAP_B + 200)
    entry = f"## {heading}\nRULE: [route] bulk-{pad}\n"
    tail_pad = "y" * (JOURNAL_HEAD_BYTES - len(entry.encode("utf-8")) + 100)
    return entry + tail_pad


def test_hop_lessons_over_budget_flag_keeps_lane_entry_heading() -> None:
    """Trim before prepending journal flags so the over-budget line is not treated as preamble."""
    journal = _over_cap_lane_12286_journal()
    fields = hop_journal_projection_fields(
        journal_text=journal,
        lane="12286",
        register_text="",
    )
    lessons = fields["lessons_in_force"]
    assert fields.get("journal_over_budget_flag") or "[journal_over_budget" in lessons
    assert "entry 341" in lessons
    assert "RULE: [route]" in lessons
    assert lessons.strip() != "[journal_over_budget"


def test_hop_lessons_inflight_line_keeps_lane_entry_heading() -> None:
    """Trim before in-flight insert so the projection line is not split as a lesson entry."""
    journal = _over_cap_lane_12286_journal()

    def _lookup() -> str:
        return "exec-proj-123"

    lessons = hop_journal_projection_fields(
        journal_text=journal,
        lane="12286",
        register_text="",
        inflight_projection_lookup=_lookup,
    )["lessons_in_force"]
    assert "entry 341" in lessons
    assert "In-flight journal projection: exec-proj-123" in lessons
    assert "RULE: [route]" in lessons
