"""In-process fixture replay via MonitorController hub (no seed/tick)."""

from __future__ import annotations

import pytest

from scripts.model_manager.ui.dispatch_monitor.core.replay import JsonlEventSource
from scripts.model_manager.ui.dispatch_monitor.core.tests.conftest import (
    FIXTURE_NAMES,
    fixture_path,
)
from scripts.model_manager.ui.dispatch_monitor.ulg.controller import MonitorController
from scripts.model_manager.ui.dispatch_monitor.ulg import fixture_view
from scripts.model_manager.ui.dispatch_monitor.ulg import seeder as seeder_mod
from scripts.model_manager.ui.dispatch_monitor.ulg import terminal_backfill as backfill_mod


def test_replay_publishes_only_when_fingerprint_changes() -> None:
    path = fixture_path("charter-admit-run-terminal.jsonl")
    source = JsonlEventSource.from_path(path)
    record_count = len(source.records)

    frames: list = []
    controller = MonitorController()
    fixture_view.subscribe_paint(controller, frames.append)
    applied, published = fixture_view.replay_fixture_path(controller, path)

    assert applied == record_count
    # One duplicate ``scanned`` redelivery is ingest-only; fingerprint must not move.
    assert published == record_count - 1
    assert len(frames) == published
    fingerprints = [frame.fingerprint for frame in frames]
    assert len(fingerprints) == len(set(fingerprints))


@pytest.mark.parametrize("fixture_name", FIXTURE_NAMES)
def test_replay_never_calls_seed_or_tick_backfill(
    monkeypatch: pytest.MonkeyPatch, fixture_name: str
) -> None:
    def _boom(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("live Event Service path must not run during fixture replay")

    monkeypatch.setattr(seeder_mod, "seed_model", _boom)
    monkeypatch.setattr(backfill_mod, "backfill_sdk_fold", _boom)

    frames: list = []
    controller = MonitorController()
    fixture_view.subscribe_paint(controller, frames.append)
    applied, published = fixture_view.replay_fixture_path(
        controller, fixture_path(fixture_name)
    )

    source = JsonlEventSource.from_path(fixture_path(fixture_name))
    assert applied == len(source.records)
    assert published >= 1
    assert len(frames) == published
