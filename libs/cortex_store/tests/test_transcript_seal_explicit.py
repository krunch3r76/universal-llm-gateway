"""Seal op must not reopen agent-bus sqlite when caller passes explicit ids."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from cortex_store.dispatch_ops.ops_transcript_seal import _op_transcript_seal

pytestmark = pytest.mark.offline


@patch("cortex_store.dispatch_ops.ops_transcript_discover.explicit_uuids_for_lane")
@patch("cortex_store.dispatch_ops.ops_transcript_seal.lane_touches")
@patch("cortex_store.dispatch_ops.ops_transcript_seal.binding_for")
@patch("cortex_store.dispatch_ops.ops_transcript_seal.resolve_jsonl_path")
def test_op_transcript_seal_trusts_caller_explicit_ids(
    mock_resolve_path,
    mock_binding_for,
    mock_lane_touches,
    mock_explicit_for_lane,
) -> None:
    mock_resolve_path.return_value = Path(
        "/tmp/transcripts/uuid-premerged/uuid-premerged.jsonl"
    )
    mock_lane_touches.return_value = {}
    mock_binding_for.return_value = ("no_touch", None)

    _op_transcript_seal(
        thread="10223",
        jsonl_path="uuid-premerged/uuid-premerged.jsonl",
        explicit_transcript_ids=["uuid-premerged"],
    )

    mock_explicit_for_lane.assert_not_called()
    assert mock_binding_for.call_args.kwargs["explicit_uuids"] == {"uuid-premerged"}
