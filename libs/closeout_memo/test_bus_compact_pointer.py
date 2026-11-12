"""Closeout memo bus pointer formatting."""

from __future__ import annotations

import pytest

from closeout_memo.bus_pointer import compact_overflow_bus_body
from closeout_memo.models import CloseoutMemoRequest

pytestmark = pytest.mark.offline


def _memo() -> CloseoutMemoRequest:
    return CloseoutMemoRequest(
        memo_key="giw:lane-23222",
        kind="sdk_closeout",
        status="completed",
        wake_lane="23222",
        worker_thread="23222",
        dispatch_thread="23213",
        dispatch_id="disp-abc",
        execution_id="exec-def",
        sidecar="cortex://notes/closeout/h.md",
        emitted_at="2026-11-12T10:00:00Z",
    )


def test_compact_overflow_bus_body_section_map() -> None:
    overflow = "## summary\n\nDone.\n## tests\n\npytest ok\n"
    body = compact_overflow_bus_body(memo=_memo(), overflow_markdown=overflow)
    assert len(body) <= 2000
    assert "sha256:" in body
    assert "cortex://notes/closeout/h.md" in body
    assert "## sections" in body
