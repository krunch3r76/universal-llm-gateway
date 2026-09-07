"""Tests for transcript_seal dispatch op."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from cortex_store.dispatch_ops.ops_transcript_seal import _op_transcript_seal

pytestmark = pytest.mark.offline


def test_seal_requires_thread_and_jsonl() -> None:
    assert _op_transcript_seal(thread="1").get("code") == "transcript_seal.missing_jsonl"
    assert (
        _op_transcript_seal(jsonl_path="x/y.jsonl").get("code")
        == "transcript_seal.missing_thread"
    )


@patch("cortex_store.session_close_successor_hop.lookup_sealed_journal")
@patch("cortex_store.dispatch_ops.ops_transcript_seal.derive_session_id_from_jsonl_start")
@patch("cortex_store.dispatch_ops.ops_transcript_seal.resolve_jsonl_path")
def test_seal_already_closed(mock_resolve, mock_derive, mock_lookup, tmp_path) -> None:
    p = tmp_path / "u" / "u.jsonl"
    p.parent.mkdir(parents=True)
    p.write_text("{}\n", encoding="utf-8")
    mock_resolve.return_value = p
    mock_derive.return_value = "cursor-2026-09-07-120000-abc"
    mock_lookup.return_value = object()
    result = _op_transcript_seal(thread="100", jsonl_path=str(p))
    assert result.get("code") == "transcript_seal.already_closed"
