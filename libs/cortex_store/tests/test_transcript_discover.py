"""Tests for transcript_discover dispatch op."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from cortex_store.dispatch_ops.ops_transcript_discover import _op_transcript_discover

pytestmark = pytest.mark.offline


def test_discover_requires_thread() -> None:
    result = _op_transcript_discover()
    assert result.get("code") == "tape.missing_thread"


@patch("cortex_store.dispatch_ops.ops_transcript_discover._thread_detail")
def test_discover_not_root(mock_detail) -> None:
    mock_detail.return_value = {
        "id": "999",
        "tags": [],
        "created_at": "2026-09-07T00:00:00Z",
    }
    result = _op_transcript_discover(thread="999")
    assert result.get("code") == "tape.not_root"


@patch("cortex_store.dispatch_ops.ops_transcript_discover._discover_open_windows")
@patch("cortex_store.dispatch_ops.ops_transcript_discover._thread_detail")
def test_discover_root_returns_open_windows(mock_detail, mock_windows) -> None:
    mock_detail.return_value = {
        "id": "100",
        "tags": ["role:root"],
        "created_at": "2026-09-07T00:00:00Z",
    }
    mock_windows.return_value = (
        [{"transcript_id": "uuid-1", "binding": "dominant_write"}],
        [],
        {"read_only": 0, "foreign_dominant": 0, "no_touch": 0, "dropped": 0, "segment_unavailable": 0},
    )
    result = _op_transcript_discover(thread="100")
    assert result["thread_id"] == "100"
    assert result["open_window_count"] == 1
