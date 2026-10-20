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
    call_order: list[str] = []

    def _sub(*a, **k):  # type: ignore[no-untyped-def]
        call_order.append("subscribe")
        return {"ok": True}

    def _cleanup(*a, **k):  # type: ignore[no-untyped-def]
        call_order.append("cleanup")
        return {"ok": True}

    with (
        patch("tools.continuity_ops.is_cursor_xvfb_host", return_value=True),
        patch(
            "tools.continuity_ops.cleanup_xvfb_sidebar_on_holder_claim",
            side_effect=_cleanup,
        ),
        patch(
            "tools.continuity_ops.subscribe_cursor_xvfb_house",
            side_effect=_sub,
        ),
    ):
        out = maybe_subscribe_cursor_xvfb_on_resume(
            thread="18374",
            transcript_id="db36e9f8-e76f-49e7-bd57-c9c4ffa88195",
            surface="cursor",
            resume_result={"fence_id": "f1"},
        )
    assert call_order == ["subscribe", "cleanup"]
    assert out["xvfb_subscription"]["ok"] is True


def test_maybe_subscribe_cleanup_failure_still_subscribes() -> None:
    call_order: list[str] = []

    def _sub(*a, **k):  # type: ignore[no-untyped-def]
        call_order.append("subscribe")
        return {"ok": True, "agent_id": "db36e9f8-e76f-49e7-bd57-c9c4ffa88195"}

    def _cleanup(*a, **k):  # type: ignore[no-untyped-def]
        call_order.append("cleanup")
        raise TimeoutError("cdp")

    with (
        patch("tools.continuity_ops.is_cursor_xvfb_host", return_value=True),
        patch(
            "tools.continuity_ops.cleanup_xvfb_sidebar_on_holder_claim",
            side_effect=_cleanup,
        ),
        patch(
            "tools.continuity_ops.subscribe_cursor_xvfb_house",
            side_effect=_sub,
        ),
    ):
        out = maybe_subscribe_cursor_xvfb_on_resume(
            thread="18374",
            transcript_id="db36e9f8-e76f-49e7-bd57-c9c4ffa88195",
            surface="cursor",
            resume_result={"fence_id": "f1"},
        )
    assert call_order == ["subscribe", "cleanup"]
    assert out["xvfb_subscription"]["ok"] is True
    assert out["xvfb_sidebar_cleanup"] == {
        "ok": False,
        "reason": "cleanup_failed:TimeoutError",
    }


def test_maybe_subscribe_skips_non_xvfb_host() -> None:
    with patch("tools.continuity_ops.is_cursor_xvfb_host", return_value=False):
        out = maybe_subscribe_cursor_xvfb_on_resume(
            thread="18374",
            transcript_id="db36e9f8-e76f-49e7-bd57-c9c4ffa88195",
            surface="cursor",
            resume_result={"fence_id": "f1"},
        )
    assert "xvfb_subscription" not in out
