"""continuity_ops Xvfb auto-subscribe on resume and checkpoint."""

from __future__ import annotations

import ast
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import ANY, patch

import pytest

from tools.continuity_ops import (
    maybe_subscribe_cursor_xvfb_on_checkpoint,
    maybe_subscribe_cursor_xvfb_on_resume,
)

pytestmark = pytest.mark.offline

_TID = "db36e9f8-e76f-49e7-bd57-c9c4ffa88195"


@contextmanager
def _rebind_noop():
    with (
        patch(
            "tools.continuity_ops.current_root_subscription_agent_id",
            return_value=None,
        ),
        patch(
            "tools.continuity_ops.rebind_predecessor_subscriptions",
            return_value={"ok": True, "repointed": []},
        ),
    ):
        yield


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
        _rebind_noop(),
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
        _rebind_noop(),
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
        _rebind_noop(),
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


def test_maybe_subscribe_prefers_displaced_holder_for_rebind() -> None:
    prior_tab = "aaaa1111-1111-4111-8111-111111111111"
    with (
        patch("tools.continuity_ops.detect_cursor_gui_host", return_value=":8"),
        patch("tools.continuity_ops.is_cursor_xvfb_host", return_value=True),
        patch(
            "tools.continuity_ops.claim_fable_lock",
            return_value={"ok": True, "displaced_holder": f"ide:{prior_tab}"},
        ),
        patch(
            "tools.continuity_ops.cleanup_xvfb_sidebar_on_holder_claim",
            return_value={"ok": True},
        ),
        patch(
            "tools.continuity_ops.subscribe_cursor_xvfb_house",
            return_value={"ok": True},
        ),
        patch(
            "tools.continuity_ops.rebind_predecessor_subscriptions",
            return_value={"ok": True, "repointed": []},
        ) as mock_rebind,
        patch(
            "tools.continuity_ops.current_root_subscription_agent_id",
            return_value="should-not-use",
        ),
        patch("tools.continuity_ops._count_subscribed_root_heads", return_value=1),
    ):
        maybe_subscribe_cursor_xvfb_on_resume(
            thread="18528",
            transcript_id=_TID,
            surface="cursor",
            resume_result={"fence_id": "f1"},
        )
    assert mock_rebind.call_args.kwargs["prior_agent_id"] == prior_tab


def test_maybe_subscribe_ambiguous_prior_skips_rebind() -> None:
    with (
        patch("tools.continuity_ops.detect_cursor_gui_host", return_value=":8"),
        patch("tools.continuity_ops.is_cursor_xvfb_host", return_value=True),
        patch("tools.continuity_ops.claim_fable_lock", return_value={"ok": True}),
        patch(
            "tools.continuity_ops.cleanup_xvfb_sidebar_on_holder_claim",
            return_value={"ok": True},
        ),
        patch(
            "tools.continuity_ops.subscribe_cursor_xvfb_house",
            return_value={"ok": True},
        ),
        patch(
            "tools.continuity_ops.rebind_predecessor_subscriptions",
            return_value={"ok": True},
        ) as mock_rebind,
        patch(
            "tools.continuity_ops.current_root_subscription_agent_id",
            return_value="other-tab",
        ),
        patch("tools.continuity_ops._count_subscribed_root_heads", return_value=2),
    ):
        out = maybe_subscribe_cursor_xvfb_on_resume(
            thread="18528",
            transcript_id=_TID,
            surface="cursor",
            resume_result={"fence_id": "f1"},
        )
    mock_rebind.assert_not_called()
    assert out["child_lane_rebind"] == {"ok": False, "reason": "ambiguous_prior"}


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
        _rebind_noop(),
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
        _rebind_noop(),
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


def test_subscribe_xvfb_tab_gui_host_unresolved() -> None:
    from tools.continuity_ops import _subscribe_xvfb_tab

    with patch("tools.continuity_ops.detect_cursor_gui_host", return_value=None):
        out = _subscribe_xvfb_tab(
            "22502",
            "7689373a-2499-41f0-9ff6-fccbcf130362",
            bound_by="test",
            evidence="test",
        )
    assert out == {"ok": False, "reason": "gui_host_unresolved"}


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


def test_continuity_ops_does_not_import_cursor_agents_hop() -> None:
    path = Path(__file__).resolve().parent / "continuity_ops.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    forbidden = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            mod = node.module
            if mod == "cursor_bridge.cursor_agents_hop" or mod.startswith(
                "cursor_bridge.cursor_agents_hop."
            ):
                forbidden.append(ast.get_source_segment(path.read_text(encoding="utf-8"), node))
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("cursor_bridge.cursor_agents_hop"):
                    forbidden.append(alias.name)
    assert forbidden == []
