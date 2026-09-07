"""SF1: nested implement commit witness."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from services.git_integration_worker.cursor_sdk_nested_witness import (
    _nested_child_has_commits,
    nested_implement_has_commits,
)

pytestmark = pytest.mark.offline


def test_nested_child_has_commits_from_closeout_body() -> None:
    assert (
        _nested_child_has_commits(
            dispatch_id="child-1",
            contract="implement",
            status="completed",
            record_json=json.dumps(
                {
                    "closeout_body": (
                        '{"status":"complete","commits_ahead":2,"head_sha":"abc"}'
                    )
                }
            ),
            wt_baseline=json.dumps({"admit_head": "deadbeef"}),
            source_repo=None,
            worktree_path=None,
        )
        is True
    )


def test_nested_child_has_commits_false_for_zero_commits() -> None:
    assert (
        _nested_child_has_commits(
            dispatch_id="child-2",
            contract="implement",
            status="completed",
            record_json=json.dumps(
                {"closeout_body": '{"status":"complete","commits_ahead":0}'}
            ),
            wt_baseline=json.dumps({"admit_head": "deadbeef"}),
            source_repo=None,
            worktree_path=None,
        )
        is False
    )


def test_nested_implement_has_commits_reads_ledger_children() -> None:
    row = {
        "dispatch_id": "child-nested-sf1",
        "contract": "implement",
        "status": "completed",
        "record_json": json.dumps(
            {
                "closeout_body": '{"status":"complete","commits_ahead":1}',
            }
        ),
        "wt_baseline": json.dumps({"admit_head": "deadbeef"}),
        "source_repo": None,
        "worktree_path": None,
    }
    conn = MagicMock()
    conn.execute.return_value.fetchone.return_value = row
    conn.__enter__.return_value = conn
    conn.__exit__.return_value = None
    ledger = MagicMock()
    ledger.list_nested_children.return_value = ["child-nested-sf1"]
    ledger._connect.return_value = conn
    with patch(
        "services.git_integration_worker.cursor_dispatch_ledger.CursorDispatchLedger"
    ) as ledger_cls:
        ledger_cls.instance.return_value = ledger
        assert (
            nested_implement_has_commits(nest_under_dispatch_id="parent-conductor-sf1")
            is True
        )
