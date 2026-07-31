"""cortex.assertion.deserialize_skipped telemetry."""

from __future__ import annotations

import pytest

from cortex_store.assertion_deserialize_telemetry import (
    assertion_deserialize_skip_reason,
    emit_assertion_deserialize_skipped,
)


def test_emit_assertion_deserialize_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    recorded: list[tuple[str, dict]] = []

    def _fake_record(signal: str, **payload: object) -> None:
        recorded.append((signal, dict(payload)))

    monkeypatch.setattr(
        "cortex_store.assertion_deserialize_telemetry.record",
        _fake_record,
    )
    emit_assertion_deserialize_skipped(
        entity_id="todo:x",
        assertion_id=42,
        reason=assertion_deserialize_skip_reason(ValueError("bad row")),
    )
    assert len(recorded) == 1
    signal, payload = recorded[0]
    assert signal == "cortex.assertion.deserialize_skipped"
    assert payload == {
        "entity_id": "todo:x",
        "assertion_id": 42,
        "reason": "ValueError",
    }
