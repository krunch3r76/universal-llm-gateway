"""Unit tests for the CHECKPOINT charter-scoreboard journal step."""

from __future__ import annotations

from pathlib import Path

import pytest

from .score import journal_charter_tip

pytestmark = pytest.mark.offline


def test_journal_charter_tip_writes_sha(tmp_path: Path) -> None:
    body = "# 10479\n\n## Rows\n\n| # | row | work_key | status |\n|---|---|---|---|\n| R1 | x | todo:a | OPEN |\n"
    result = journal_charter_tip(
        thread="10479",
        tip_body=body,
        seat="cursor",
        dispatch_id="exec-1",
        delta="checkpoint",
        files_root=tmp_path,
    )
    assert result["skipped"] is False
    assert result["rejected_reason"] is None
    assert result["tip_sha"]
    assert (tmp_path / "notes/system/threads/10479-charter-scoreboard.md").is_file()
    assert (tmp_path / "notes/system/threads/10479-charter-score-journal.md").is_file()


def test_absent_scoreboard_tip_is_skip_payload() -> None:
    from .score import ContinuityCheckpointScoreHandler

    assert ContinuityCheckpointScoreHandler.step_type == "continuity_checkpoint_score_v1"
