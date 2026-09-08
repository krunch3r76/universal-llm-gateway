"""Tests for transcript discover boundary — explicit ids owned by caller."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from cortex_store.dispatch_ops.ops_transcript_discover import (
    _discover_open_windows,
    _op_transcript_discover,
)

pytestmark = pytest.mark.offline


@patch("cortex_store.dispatch_ops.ops_transcript_discover.explicit_uuids_for_lane")
@patch("cortex_store.dispatch_ops.ops_transcript_discover._jsonl_paths_by_mtime_desc")
@patch("cortex_store.dispatch_ops.ops_transcript_discover._transcripts_root")
def test_discover_open_windows_does_not_merge_cp_anchors(
    mock_root,
    mock_paths,
    mock_explicit_for_lane,
) -> None:
    """Pre-passed explicit ids are authoritative — no agent_bus DB from discover."""
    mock_root.return_value = mock_root
    mock_paths.return_value = []
    explicit = {"uuid-premerged"}

    _discover_open_windows(
        thread_id="10223",
        lane_created_at=datetime.min.replace(tzinfo=UTC),
        explicit_uuids=explicit,
    )

    mock_explicit_for_lane.assert_not_called()


@patch("cortex_store.dispatch_ops.ops_transcript_discover._discover_open_windows")
@patch("cortex_store.dispatch_ops.ops_transcript_discover.explicit_uuids_for_lane")
@patch("cortex_store.dispatch_ops.ops_transcript_discover._thread_detail")
def test_op_transcript_discover_merges_cp_anchors_when_omitted(
    mock_detail,
    mock_explicit_for_lane,
    mock_discover,
) -> None:
    mock_detail.return_value = {
        "created_at": "2026-01-01T00:00:00Z",
        "tags": ["role:root"],
    }
    mock_explicit_for_lane.return_value = {"uuid-cp"}
    mock_discover.return_value = ([], [], {})

    _op_transcript_discover(thread="10223")

    mock_explicit_for_lane.assert_called_once_with("10223", set())
    assert mock_discover.call_args.kwargs["explicit_uuids"] == {"uuid-cp"}


@patch("cortex_store.dispatch_ops.ops_transcript_discover._discover_open_windows")
@patch("cortex_store.dispatch_ops.ops_transcript_discover.explicit_uuids_for_lane")
@patch("cortex_store.dispatch_ops.ops_transcript_discover._thread_detail")
def test_op_transcript_discover_trusts_caller_explicit_ids(
    mock_detail,
    mock_explicit_for_lane,
    mock_discover,
) -> None:
    mock_detail.return_value = {
        "created_at": "2026-01-01T00:00:00Z",
        "tags": ["role:root"],
    }
    mock_discover.return_value = ([], [], {})

    _op_transcript_discover(thread="10223", explicit_transcript_ids=["uuid-extra"])

    mock_explicit_for_lane.assert_not_called()
    assert mock_discover.call_args.kwargs["explicit_uuids"] == {"uuid-extra"}
