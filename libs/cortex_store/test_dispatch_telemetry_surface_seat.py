"""Dispatch telemetry surface/seat pass-through (day-zero instrumentation)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from cortex_store.dispatch_ops import execute_op


def _capture_records(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict]]:
    captured: list[tuple[str, dict]] = []

    def _fake_record(signal: str, **payload: object) -> None:
        captured.append((signal, dict(payload)))

    monkeypatch.setattr("cortex_store.dispatch_ops.record", _fake_record)
    return captured


@pytest.mark.offline
def test_dispatch_emits_surface_and_seat(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _capture_records(monkeypatch)
    with patch("cortex_store.dispatch_ops.ops_misc._op_stats", return_value={"ok": True}):
        result = execute_op(
            "stats",
            {},
            surface="life",
            seat="cursor_safe",
            via_adapter=True,
        )
    assert "error" not in result
    dispatch = [p for sig, p in captured if sig == "mcp.cortex.dispatch"]
    assert len(dispatch) == 1
    assert dispatch[0]["tool"] == "stats"
    assert dispatch[0]["surface"] == "life"
    assert dispatch[0]["seat"] == "cursor_safe"
    assert dispatch[0]["via_adapter"] is True


@pytest.mark.offline
def test_dispatch_omits_empty_telemetry_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _capture_records(monkeypatch)
    with patch("cortex_store.dispatch_ops.ops_misc._op_stats", return_value={"ok": True}):
        execute_op("stats", {})
    dispatch = [p for sig, p in captured if sig == "mcp.cortex.dispatch"][0]
    assert "surface" not in dispatch
    assert "seat" not in dispatch
    assert "via_adapter" not in dispatch
