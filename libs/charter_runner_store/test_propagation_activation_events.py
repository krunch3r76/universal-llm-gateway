"""Tests for activation event factories and UDS publication."""

from __future__ import annotations

import json

from charter_runner_store.propagation_activation_events import (
    ManageRestartActivationProgress,
    ManageRestartVerifying,
    publish_activation_event,
)


def test_publish_activation_event_writes_ndjson(tmp_path, monkeypatch) -> None:
    import charter_runner_store.propagation_activation_events as mod

    sock = tmp_path / "events.sock"
    monkeypatch.setattr(mod, "_EVENTS_SOCK", str(sock))
    event = ManageRestartVerifying(
        intent_id="intent-1",
        validation_id="val-1",
        service="git_integration_worker",
        kill_boundary_at="2026-08-15T00:00:00+00:00",
        boundary_source="kill_return",
    )
    publish_activation_event(event)
    lines = sock.read_text().strip().splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["signal"] == "manage.restart.verifying"
    assert payload["role"] == "observation"
    assert payload["scope"] == "node"
    assert payload["payload"]["intent_id"] == "intent-1"


def test_progress_factory_signal() -> None:
    event = ManageRestartActivationProgress(
        intent_id="i",
        validation_id="v",
        progress_class="reachable",
    )
    assert event.signal == "manage.restart.activation.progress"
