"""Facade routing and pass-through tests."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from chat_harvest.models import ClassifyRefuse
from interlocutor import idempotency
from interlocutor.facade import execute_read, execute_steer
from interlocutor.targets import MAESTRO_COWORK_PARENT_THREAD, resolve_target

pytestmark = pytest.mark.offline


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
