"""Tests for cursor-sdk knob derivation from executor override."""

from __future__ import annotations

from agent_seat.cursor_sdk_knobs import derive_model_knobs


def test_derive_model_knobs_default_none() -> None:
    assert derive_model_knobs() is None
    assert derive_model_knobs(executor_override="composer") is None


def test_derive_model_knobs_fast_explicit() -> None:
    assert derive_model_knobs(executor_override="composer-fast") == {"fast": "true"}


def test_derive_model_knobs_thinking_slows() -> None:
    assert derive_model_knobs(executor_override="composer-thinking") == {
        "fast": "false"
    }


def test_derive_model_knobs_packet_override() -> None:
    assert derive_model_knobs(packet_executor_override="composer-thinking") == {
        "fast": "false"
    }
