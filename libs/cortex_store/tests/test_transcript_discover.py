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


@patch("cortex_store.dispatch_ops.ops_transcript_discover.jsonl_path_for_uuid")
@patch("cortex_store.dispatch_ops.ops_transcript_discover.explicit_uuids_for_lane")
@patch("cortex_store.dispatch_ops.ops_transcript_discover._jsonl_paths_by_mtime_desc")
@patch("cortex_store.dispatch_ops.ops_transcript_discover._transcripts_root")
def test_discover_open_windows_does_not_merge_cp_anchors(
    mock_root,
    mock_paths,
    mock_explicit_for_lane,
    mock_jsonl_for_uuid,
) -> None:
    """Pre-passed explicit ids are authoritative — no agent_bus DB from discover."""
    from pathlib import Path

    mock_root.return_value = Path("/tmp/transcripts-anchor")
    mock_jsonl_for_uuid.return_value = Path("/tmp/transcripts-anchor/uuid-premerged/uuid-premerged.jsonl")
    explicit = {"uuid-premerged"}

    _discover_open_windows(
        thread_id="10223",
        lane_created_at=datetime.min.replace(tzinfo=UTC),
        explicit_uuids=explicit,
    )

    mock_explicit_for_lane.assert_not_called()
    mock_paths.assert_not_called()


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


@patch("cortex_store.dispatch_ops.ops_transcript_discover.lane_touches")
@patch("cortex_store.dispatch_ops.ops_transcript_discover.binding_for")
@patch("cortex_store.dispatch_ops.ops_transcript_discover.lookup_sealed_journal")
@patch("cortex_store.dispatch_ops.ops_transcript_discover.derive_session_id_from_jsonl_start")
@patch("cortex_store.dispatch_ops.ops_transcript_discover.jsonl_path_for_uuid")
@patch("cortex_store.dispatch_ops.ops_transcript_discover._transcripts_root")
def test_explicit_ids_use_direct_path_not_root_scan(
    mock_root,
    mock_jsonl_for_uuid,
    mock_derive_session,
    mock_lookup,
    mock_binding_for,
    mock_lane_touches,
) -> None:
    from pathlib import Path

    root = Path("/tmp/transcripts")
    mock_root.return_value = root
    jsonl = root / "uuid-a" / "uuid-a.jsonl"
    mock_jsonl_for_uuid.return_value = jsonl
    jsonl.parent.mkdir(parents=True, exist_ok=True)
    jsonl.write_text('{"role":"user","content":"hi"}\n', encoding="utf-8")
    mock_derive_session.return_value = "cursor-2026-01-01-000000-abc"
    mock_lookup.return_value = None
    mock_lane_touches.return_value = {}
    mock_binding_for.return_value = ("explicit_cp", "10223")

    with patch(
        "cortex_store.dispatch_ops.ops_transcript_discover._jsonl_paths_by_mtime_desc"
    ) as mock_scan:
        open_windows, _, _ = _discover_open_windows(
            thread_id="10223",
            lane_created_at=datetime.min.replace(tzinfo=UTC),
            explicit_uuids={"uuid-a"},
        )
        mock_scan.assert_not_called()

    assert len(open_windows) == 1
    assert open_windows[0]["transcript_id"] == "uuid-a"


@patch("cortex_store.dispatch_ops.ops_transcript_discover.transcript_discover_filtered")
@patch("cortex_store.dispatch_ops.ops_transcript_discover._sealed_turn_count")
@patch("cortex_store.dispatch_ops.ops_transcript_discover._live_jsonl_turn_count")
@patch("cortex_store.dispatch_ops.ops_transcript_discover.lane_touches")
@patch("cortex_store.dispatch_ops.ops_transcript_discover.binding_for")
@patch("cortex_store.dispatch_ops.ops_transcript_discover.lookup_sealed_journal")
@patch("cortex_store.dispatch_ops.ops_transcript_discover.derive_session_id_from_jsonl_start")
@patch("cortex_store.dispatch_ops.ops_transcript_discover.jsonl_path_for_uuid")
@patch("cortex_store.dispatch_ops.ops_transcript_discover._transcripts_root")
def test_succession_extend_when_live_tail_grows(
    mock_root,
    mock_jsonl_for_uuid,
    mock_derive_session,
    mock_lookup,
    mock_binding_for,
    mock_lane_touches,
    mock_live_turns,
    mock_sealed_turns,
    mock_filtered,
) -> None:
    from pathlib import Path

    from cortex_store.session_close_successor_hop import SealedJournal

    root = Path("/tmp/transcripts-ext")
    mock_root.return_value = root
    jsonl = root / "uuid-b" / "uuid-b.jsonl"
    mock_jsonl_for_uuid.return_value = jsonl
    jsonl.parent.mkdir(parents=True, exist_ok=True)
    jsonl.write_text('{"role":"user","content":"hi"}\n', encoding="utf-8")
    mock_derive_session.return_value = "cursor-2026-01-01-000000-def"
    mock_lookup.return_value = SealedJournal(
        session_id="cursor-2026-01-01-000000-def",
        journal_row_id=1,
        timestamp="2026-01-01T00:00:00Z",
        prior_session_id=None,
        closed_by="succession",
    )
    mock_live_turns.return_value = 10
    mock_sealed_turns.return_value = 5
    mock_lane_touches.return_value = {}
    mock_binding_for.return_value = ("explicit_cp", "10223")

    open_windows, excluded, counts = _discover_open_windows(
        thread_id="10223",
        lane_created_at=datetime.min.replace(tzinfo=UTC),
        explicit_uuids={"uuid-b"},
    )

    assert len(open_windows) == 1
    assert open_windows[0]["extend"] is True
    assert counts["quiescent"] == 0


@patch("cortex_store.dispatch_ops.ops_transcript_discover.transcript_discover_filtered")
@patch("cortex_store.dispatch_ops.ops_transcript_discover._sealed_turn_count")
@patch("cortex_store.dispatch_ops.ops_transcript_discover._live_jsonl_turn_count")
@patch("cortex_store.dispatch_ops.ops_transcript_discover.jsonl_path_for_uuid")
@patch("cortex_store.dispatch_ops.ops_transcript_discover._transcripts_root")
def test_succession_quiescent_when_no_new_turns(
    mock_root,
    mock_jsonl_for_uuid,
    mock_live_turns,
    mock_sealed_turns,
    mock_filtered,
) -> None:
    from pathlib import Path

    from cortex_store.session_close_successor_hop import SealedJournal

    root = Path("/tmp/transcripts-quiet")
    mock_root.return_value = root
    jsonl = root / "uuid-c" / "uuid-c.jsonl"
    mock_jsonl_for_uuid.return_value = jsonl
    jsonl.parent.mkdir(parents=True, exist_ok=True)
    jsonl.write_text('{"role":"user","content":"hi"}\n', encoding="utf-8")
    with patch(
        "cortex_store.dispatch_ops.ops_transcript_discover.derive_session_id_from_jsonl_start",
        return_value="cursor-2026-01-01-000000-ghi",
    ):
        with patch(
            "cortex_store.dispatch_ops.ops_transcript_discover.lookup_sealed_journal",
            return_value=SealedJournal(
                session_id="cursor-2026-01-01-000000-ghi",
                journal_row_id=2,
                timestamp="2026-01-01T00:00:00Z",
                prior_session_id=None,
                closed_by="succession",
            ),
        ):
            mock_live_turns.return_value = 5
            mock_sealed_turns.return_value = 5
            open_windows, _, counts = _discover_open_windows(
                thread_id="10223",
                lane_created_at=datetime.min.replace(tzinfo=UTC),
                explicit_uuids={"uuid-c"},
            )

    assert open_windows == []
    assert counts["quiescent"] == 1
