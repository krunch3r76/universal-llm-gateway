"""Tests for expand.consume.routed event factory."""

from __future__ import annotations

from events.prompt_expand_consume import (
    ExpandConsumeRouted,
    emit_expand_consume_routed,
    parse_consume_reason_fields,
)


def test_expand_consume_routed_signal() -> None:
    event = ExpandConsumeRouted(
        execution_id="exec-1",
        dispatch_id="disp-1",
        door="stargate",
        branch="in_seat",
        reason="operator_verb:window",
        operator_verb="window",
        attended=True,
        durable_session=False,
        summoning_thread_id="11806",
    )
    assert event.signal == "expand.consume.routed"
    assert event.payload["door"] == "stargate"
    assert event.payload["branch"] == "in_seat"


def test_parse_consume_reason_fields() -> None:
    assert parse_consume_reason_fields("fire_hint:in_seat") == ("in_seat", None)
    assert parse_consume_reason_fields("operator_verb:window") == (None, "window")


def test_emit_expand_consume_routed_publishes(monkeypatch) -> None:
    captured: list[object] = []

    def _capture(event: object) -> None:
        captured.append(event)

    monkeypatch.setattr(
        "events.prompt_expand_consume._publish",
        _capture,
    )
    emit_expand_consume_routed(
        execution_id="exec-2",
        dispatch_id="disp-2",
        door="giw",
        branch="sdk_background",
        reason="default",
        attended=False,
        durable_session=False,
    )
    assert len(captured) == 1
    assert captured[0].signal == "expand.consume.routed"
