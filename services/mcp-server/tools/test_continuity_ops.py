"""continuity_ops Xvfb auto-subscribe on resume and checkpoint."""

from __future__ import annotations

from unittest.mock import ANY, patch

import pytest

from tools.continuity_ops import (
    maybe_subscribe_cursor_xvfb_on_checkpoint,
    maybe_subscribe_cursor_xvfb_on_resume,
)

pytestmark = pytest.mark.offline

_TID = "db36e9f8-e76f-49e7-bd57-c9c4ffa88195"


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

    def _claim(*a, **k):  # type: ignore[no-untyped-def]
        call_order.append("claim")
        return {"ok": True}

    def _sub(*a, **k):  # type: ignore[no-untyped-def]
        call_order.append("subscribe")
        return {"ok": True}

    def _cleanup(*a, **k):  # type: ignore[no-untyped-def]
        call_order.append("cleanup")
        return {"ok": True}

    with (
        patch("tools.continuity_ops.detect_cursor_gui_host", return_value=":8"),
        patch("tools.continuity_ops.is_cursor_xvfb_host", return_value=True),
        patch("tools.continuity_ops.claim_fable_lock", side_effect=_claim),
        patch(
            "tools.continuity_ops.cleanup_xvfb_sidebar_on_holder_claim",
            side_effect=_cleanup,
        ),
        patch(
            "tools.continuity_ops.subscribe_cursor_xvfb_house",
            side_effect=_sub,
        ) as mock_sub,
    ):
        out = maybe_subscribe_cursor_xvfb_on_resume(
            thread="18374",
            transcript_id=_TID,
            surface="cursor",
            resume_result={"fence_id": "f1"},
        )
    assert call_order == ["claim", "subscribe", "cleanup"]
    assert out["xvfb_subscription"]["ok"] is True
    assert out["fable_lock_claim"] == {"ok": True}
    mock_sub.assert_called_once()
    assert mock_sub.call_args.kwargs["display"] == "8"
    assert mock_sub.call_args.kwargs["bound_by"] == "continuity-resume"


def test_maybe_subscribe_display_orion_node() -> None:
    with (
        patch("tools.continuity_ops.detect_cursor_gui_host", return_value="orion-node"),
        patch("tools.continuity_ops.is_cursor_xvfb_host", return_value=True),
        patch("tools.continuity_ops.claim_fable_lock", return_value={"ok": True}),
        patch(
            "tools.continuity_ops.cleanup_xvfb_sidebar_on_holder_claim",
            return_value={"ok": True},
        ),
        patch(
            "tools.continuity_ops.subscribe_cursor_xvfb_house",
            return_value={"ok": True},
        ) as mock_sub,
    ):
        maybe_subscribe_cursor_xvfb_on_resume(
            thread="18528",
            transcript_id=_TID,
            surface="cursor",
            resume_result={"fence_id": "f1"},
        )
    assert mock_sub.call_args.kwargs["display"] == "orion-node"


def test_maybe_subscribe_claim_failure_still_subscribes() -> None:
    with (
        patch("tools.continuity_ops.detect_cursor_gui_host", return_value=":8"),
        patch("tools.continuity_ops.is_cursor_xvfb_host", return_value=True),
        patch(
            "tools.continuity_ops.claim_fable_lock",
            side_effect=ValueError("busy"),
        ),
        patch(
            "tools.continuity_ops.cleanup_xvfb_sidebar_on_holder_claim",
            return_value={"ok": True},
        ),
        patch(
            "tools.continuity_ops.subscribe_cursor_xvfb_house",
            return_value={"ok": True},
        ),
    ):
        out = maybe_subscribe_cursor_xvfb_on_resume(
            thread="18374",
            transcript_id=_TID,
            surface="cursor",
            resume_result={"fence_id": "f1"},
        )
    assert out["fable_lock_claim"] == {
        "ok": False,
        "reason": "claim_failed:ValueError",
    }
    assert out["xvfb_subscription"]["ok"] is True


def test_maybe_subscribe_claim_fable_lock_args() -> None:
    with (
        patch("tools.continuity_ops.detect_cursor_gui_host", return_value=":8"),
        patch("tools.continuity_ops.is_cursor_xvfb_host", return_value=True),
        patch(
            "tools.continuity_ops.claim_fable_lock",
            return_value={"ok": True},
        ) as mock_claim,
        patch(
            "tools.continuity_ops.cleanup_xvfb_sidebar_on_holder_claim",
            return_value={"ok": True},
        ),
        patch(
            "tools.continuity_ops.subscribe_cursor_xvfb_house",
            return_value={"ok": True},
        ),
    ):
        maybe_subscribe_cursor_xvfb_on_resume(
            thread="18528",
            transcript_id=_TID,
            surface="cursor",
            resume_result={"fence_id": "f1"},
        )
    mock_claim.assert_called_once_with(
        f"ide:{_TID}",
        hop=False,
        root_id="18528",
        take_over=True,
    )


def test_maybe_subscribe_cleanup_failure_still_subscribes() -> None:
    call_order: list[str] = []

    def _sub(*a, **k):  # type: ignore[no-untyped-def]
        call_order.append("subscribe")
        return {"ok": True, "agent_id": _TID}

    def _cleanup(*a, **k):  # type: ignore[no-untyped-def]
        call_order.append("cleanup")
        raise TimeoutError("cdp")

    with (
        patch("tools.continuity_ops.detect_cursor_gui_host", return_value=":8"),
        patch("tools.continuity_ops.is_cursor_xvfb_host", return_value=True),
        patch("tools.continuity_ops.claim_fable_lock", return_value={"ok": True}),
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
            transcript_id=_TID,
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
            transcript_id=_TID,
            surface="cursor",
            resume_result={"fence_id": "f1"},
        )
    assert "xvfb_subscription" not in out


def test_maybe_subscribe_on_checkpoint() -> None:
    with (
        patch("tools.continuity_ops.detect_cursor_gui_host", return_value="orion-node"),
        patch("tools.continuity_ops.is_cursor_xvfb_host", return_value=True),
        patch(
            "tools.continuity_ops.subscribe_cursor_xvfb_house",
            return_value={"ok": True},
        ) as mock_sub,
    ):
        out = maybe_subscribe_cursor_xvfb_on_checkpoint(
            thread="18528",
            transcript_id=_TID,
            surface="cursor",
            checkpoint_result={"turn_number": 5},
        )
    assert out["turn_number"] == 5
    mock_sub.assert_called_once_with(
        "18528",
        _TID,
        display="orion-node",
        title=ANY,
        bound_by="continuity-checkpoint",
        evidence="continuity(op=checkpoint)",
    )


def test_maybe_subscribe_on_checkpoint_skips_on_error() -> None:
    base = {"error": {"code": "fail"}}
    out = maybe_subscribe_cursor_xvfb_on_checkpoint(
        thread="18528",
        transcript_id=_TID,
        surface="cursor",
        checkpoint_result=base,
    )
    assert out == base
    assert "xvfb_subscription" not in out
