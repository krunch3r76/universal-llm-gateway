"""Tests for A-1 succession structural fill routing."""

from __future__ import annotations

import pytest

from cortex_store.session_close_successor_hop import (
    SUCCESSION_FILL_REASON,
    SealedJournal,
    resolve_successor_hop,
)

pytestmark = pytest.mark.offline


def test_succession_fill_before_post_lid_hop(tmp_path) -> None:
    from cortex_store.session_close_successor_hop import resolve_successor_hop as rsh

    jsonl = tmp_path / "uuid" / "uuid.jsonl"
    jsonl.parent.mkdir(parents=True)
    jsonl.write_text("", encoding="utf-8")

    with patch_lookup(
        {
            "sealed-id": SealedJournal(
                session_id="sealed-id",
                journal_row_id=2,
                timestamp="2026-09-07T11:00:00Z",
                prior_session_id=None,
                closed_by="succession",
            ),
        }
    ):
        hop = rsh(
            supplied_session_id="boot-id",
            jsonl_start_id="sealed-id",
            jsonl_path=jsonl,
            agent="cursor",
        )
    assert hop is not None
    assert hop.hop_reason == SUCCESSION_FILL_REASON
    assert hop.session_id == "sealed-id"


class patch_lookup:
    def __init__(self, mapping: dict[str, SealedJournal | None]) -> None:
        self.mapping = mapping

    def __enter__(self):
        from unittest.mock import patch

        self._p = patch(
            "cortex_store.session_close_successor_hop.latest_journal_in_chain",
            side_effect=lambda sid: self.mapping.get(sid),
        )
        return self._p.__enter__()

    def __exit__(self, *args):
        return self._p.__exit__(*args)
