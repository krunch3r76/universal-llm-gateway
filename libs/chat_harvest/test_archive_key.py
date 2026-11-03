"""Harvest archive key identity (a:41818 — full sanitized conversation id)."""

from __future__ import annotations

import pytest

from chat_harvest.archive import archive_rel_path, sanitized_conversation_id

pytestmark = pytest.mark.offline


def test_archive_keys_differ_when_only_suffix_differs_past_conv12() -> None:
    shared = "cse_01aroapcv"
    id_a = f"{shared}AAAA"
    id_b = f"{shared}BBBB"
    assert sanitized_conversation_id(id_a)[:12] == sanitized_conversation_id(id_b)[:12]
    assert archive_rel_path("claude", id_a) != archive_rel_path("claude", id_b)
