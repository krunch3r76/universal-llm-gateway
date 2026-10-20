"""Tests for maestro journal → hop ``## Lessons in force`` projection."""

from __future__ import annotations

import pytest

from claude_bundles.journal_hop_projection import (
    hop_journal_projection_fields,
    project_lessons_in_force,
)

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
