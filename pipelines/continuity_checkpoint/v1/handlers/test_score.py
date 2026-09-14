"""Unit tests for the CHECKPOINT charter-scoreboard journal step."""

from __future__ import annotations

from pathlib import Path

import pytest
from implement_admission.conductor_score_journal import birth_scoreboard, load_journal
from implement_admission.conductor_witness_types import FoldDeps

from .score import journal_charter_tip, project_work_item_fold

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


def test_project_work_item_fold_does_not_journal(tmp_path: Path) -> None:
    slug = "phase-a-fold"
    body = "\n".join(
        [
            "# Scoreboard — todo:phase-a-fold",
            "",
            "| ID | Deliverable | Mode | Status | Stops |",
            "|---|---|---|---|---|",
            "| G1 | Architecture | — | OPEN | |",
            "| G2 | Frame | — | OPEN | |",
        ]
    )
    birth_scoreboard(slug, scoreboard_body=body, files_root=tmp_path)
    before = len(load_journal(slug, files_root=tmp_path))

    class _Cortex:
        def entity_get(self, entity_id: str, **kwargs):  # noqa: ANN001, ANN003
            return {"id": entity_id, "attributes": {"density_triage": "mechanical"}}

        def list_relationships(self, entity_id: str, *, type_id: str | None = None):  # noqa: ARG002
            return []

    deps = FoldDeps(cortex=_Cortex(), bus=None, git=None, source_ref=f"todo:{slug}", repo=tmp_path)
    result = project_work_item_fold(slug, deps=deps, files_root=tmp_path)
    assert result["skipped"] is False
    assert result["journal_applied"] is False
    assert result["row_status"]["G1"] in {"OPEN", "CLAIMED", "DONE"}
    assert len(load_journal(slug, files_root=tmp_path)) == before
