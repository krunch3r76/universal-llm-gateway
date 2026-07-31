"""Unit tests for cortex-scheme gating in cortex_expected_rels (F1 fix, friction 21186).

Verifies that plain repo paths are excluded from cortex pinned-deliverable resolution
and that cortex:// scheme entries still resolve correctly.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.offline

from services.git_integration_worker.cursor_sdk_deliverables import cortex_expected_rels


def test_cortex_expected_rels_excludes_repo_paths() -> None:
    result = cortex_expected_rels(
        [
            "libs/cortex_store/skill_suggest_rank.py",
            "libs/cortex_store/test_skill_suggest_stage_a.py",
            "services/some_service/foo.py",
        ]
    )
    assert result == []


def test_cortex_expected_rels_accepts_cortex_scheme() -> None:
    result = cortex_expected_rels(
        [
            "cortex://notes/system/threads/some-review.md",
            "libs/repo_path.py",
            "cortex:notes/other.md",
        ]
    )
    assert result == [
        "notes/system/threads/some-review.md",
        "notes/other.md",
    ]
