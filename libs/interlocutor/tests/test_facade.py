"""Facade routing and pass-through tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from chat_harvest.models import ClassifyRefuse
from interlocutor import idempotency
from interlocutor.facade import execute_read, execute_steer
from interlocutor.targets import MAESTRO_COWORK_PARENT_THREAD, resolve_target

pytestmark = pytest.mark.offline

_DISPLAY_TARGET = "display:8/test-agent"


@pytest.fixture(autouse=True)
def _clear_idempotency() -> None:
    idempotency.clear_for_tests()


def test_maestro_target_resolves_to_cowork_house_12286() -> None:
    resolved = resolve_target("maestro")
    assert not isinstance(resolved, ClassifyRefuse)
    assert resolved.kind == "cowork_maestro"  # type: ignore[union-attr]
    assert resolved.parent_thread == MAESTRO_COWORK_PARENT_THREAD  # type: ignore[union-attr]


def test_read_passes_after_turn_unchanged() -> None:
    with patch("interlocutor.facade.cowork_adapter.harvest") as harvest:
        harvest.return_value = {"ok": True}
        execute_read(target="maestro", after_turn=7)
        harvest.assert_called_once()
        assert harvest.call_args.kwargs["after_turn"] == 7


def test_steer_operator_relay_refuses_without_authorization() -> None:
    out = execute_steer(
        target="maestro",
        text="hi",
        voice="operator_relay",
        grant="operator",
        authorization_source="",
    )
    assert out["ok"] is False
    assert out["code"] == "watermark_missing_authorization"


def test_steer_self_voice_does_not_watermark_display_paste() -> None:
    with patch("interlocutor.facade.display_adapter.steer_display_sync") as steer:
        steer.return_value = {"ok": True, "watermarked": False, "voice": "self"}
        out = execute_steer(
            target="display:8/agent-1",
            text="plain",
            voice="self",
        )
        assert out["ok"] is True
        assert steer.call_args.kwargs["voice"] == "self"


def test_idempotency_returns_duplicate_receipt() -> None:
    with patch("interlocutor.facade.cowork_adapter.followup") as followup:
        followup.return_value = {"ok": True, "receipt": "first"}
        first = execute_steer(
            target="maestro",
            text="once",
            idempotency_key="k1",
        )
        second = execute_steer(
            target="maestro",
            text="once",
            idempotency_key="k1",
        )
        assert first.get("duplicate") is not True
        assert second.get("duplicate") is True
        assert followup.call_count == 1


def test_refusal_is_classify_refuse_shape() -> None:
    out = execute_steer(target="", text="x")
    assert out["ok"] is False
    assert "code" in out and "reason" in out


def test_steer_display_unverified_inner_failure_is_outer_ok_false_not_cached() -> None:
    """Inner paste failure without verified delivery must not cache as success."""
    with patch(
        "cursor_bridge.electron_chat_relay.paste_endpoint_message",
        new_callable=AsyncMock,
    ) as paste:
        paste.return_value = {
            "ok": False,
            "code": "composer_text_mismatch",
            "reason": "composer_text_mismatch",
            "pasted": False,
            "delivered": False,
        }
        out = execute_steer(
            target=_DISPLAY_TARGET,
            text="hello",
            idempotency_key="idem-mismatch",
        )
    assert out["ok"] is False
    assert out["code"] == "composer_text_mismatch"
    assert (
        idempotency.lookup(target=_DISPLAY_TARGET, idempotency_key="idem-mismatch")
        is None
    )


def test_steer_same_key_fail_then_retry_invokes_send_twice() -> None:
    """Failed idempotency key must not block a later retry from calling paste again."""
    with patch(
        "cursor_bridge.electron_chat_relay.paste_endpoint_message",
        new_callable=AsyncMock,
    ) as paste:
        paste.side_effect = [
            {
                "ok": False,
                "code": "composer_text_mismatch",
                "reason": "composer_text_mismatch",
                "pasted": False,
                "delivered": False,
            },
            {
                "ok": True,
                "delivered": True,
                "pasted": True,
                "message": "hello",
            },
        ]
        first = execute_steer(
            target=_DISPLAY_TARGET, text="hello", idempotency_key="retry-key"
        )
        second = execute_steer(
            target=_DISPLAY_TARGET, text="hello", idempotency_key="retry-key"
        )
    assert first["ok"] is False
    assert second["ok"] is True
    assert paste.await_count == 2


def test_steer_click_timeout_not_cached() -> None:
    with patch(
        "cursor_bridge.electron_chat_relay.paste_endpoint_message",
        new_callable=AsyncMock,
    ) as paste:
        paste.return_value = {
            "ok": False,
            "code": "cdp_eval_timeout",
            "reason": "cdp_eval_timeout",
            "pasted": False,
            "delivered": False,
            "clicked_unknown": True,
            "retryable": False,
        }
        out = execute_steer(
            target=_DISPLAY_TARGET,
            text="hello",
            idempotency_key="timeout-key",
        )
    assert out["ok"] is False
    assert out.get("retryable") is False
    assert (
        idempotency.lookup(target=_DISPLAY_TARGET, idempotency_key="timeout-key")
        is None
    )


def test_preseeded_bad_ok_true_inner_false_does_not_replay_success() -> None:
    idempotency.remember(
        target=_DISPLAY_TARGET,
        idempotency_key="bad-seed",
        receipt={
            "ok": True,
            "adapter": "display_eight",
            "result": {"ok": False, "reason": "composer_text_mismatch"},
        },
    )
    with patch(
        "cursor_bridge.electron_chat_relay.paste_endpoint_message",
        new_callable=AsyncMock,
    ) as paste:
        paste.return_value = {
            "ok": True,
            "delivered": True,
            "pasted": True,
            "message": "hello",
        }
        out = execute_steer(
            target=_DISPLAY_TARGET, text="hello", idempotency_key="bad-seed"
        )
    assert out.get("duplicate") is not True
    assert out["ok"] is True
    paste.assert_awaited_once()


def test_steer_active_agent_id_hop_refuses_paste() -> None:
    with patch(
        "cursor_bridge.electron_chat_relay.paste_endpoint_message",
        new_callable=AsyncMock,
    ) as paste:
        paste.return_value = {
            "ok": False,
            "code": "target_mismatch",
            "reason": "target_mismatch",
            "pasted": False,
            "delivered": False,
        }
        out = execute_steer(target=_DISPLAY_TARGET, text="hop check")
    assert out["ok"] is False
    assert out["code"] == "target_mismatch"
    paste.assert_awaited_once()


def test_steer_verified_success_replays_with_duplicate() -> None:
    with patch(
        "cursor_bridge.electron_chat_relay.paste_endpoint_message",
        new_callable=AsyncMock,
    ) as paste:
        paste.return_value = {
            "ok": True,
            "delivered": True,
            "pasted": True,
            "message": "once",
        }
        first = execute_steer(
            target=_DISPLAY_TARGET, text="once", idempotency_key="good-key"
        )
        second = execute_steer(
            target=_DISPLAY_TARGET, text="once", idempotency_key="good-key"
        )
    assert first["ok"] is True
    assert second.get("duplicate") is True
    assert paste.await_count == 1
