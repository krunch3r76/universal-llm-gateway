"""continuity_ops :8 auto-subscribe on resume."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from tools.continuity_ops import maybe_subscribe_cursor_xvfb_on_resume

pytestmark = pytest.mark.offline


def test_maybe_subscribe_skips_without_transcript_id() -> None:
    base = {"fence_id": "f1"}
    out = maybe_subscribe_cursor_xvfb_on_resume(
        thread="18374",
        transcript_id=None,
        surface="cursor",
        resume_result=base,
    )
    assert out == base
    assert "xvfb_subscription" not in out


def test_maybe_subscribe_on_resume() -> None:
    with patch(
        "tools.continuity_ops.subscribe_cursor_xvfb_house",
        return_value={"ok": True},
    ) as sub:
        out = maybe_subscribe_cursor_xvfb_on_resume(
            thread="18374",
            transcript_id="db36e9f8-e76f-49e7-bd57-c9c4ffa88195",
            surface="cursor",
            resume_result={"fence_id": "f1"},
        )
    sub.assert_called_once()
    assert out["xvfb_subscription"]["ok"] is True
