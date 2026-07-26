"""Shared fixture-replay helpers. Stdlib + pytest only."""

from __future__ import annotations

import os

import pytest

from scripts.model_manager.ui.dispatch_monitor.core.model import Model
from scripts.model_manager.ui.dispatch_monitor.core.replay import JsonlEventSource

_PACKAGE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE_DIR = os.path.join(_PACKAGE_ROOT, "fixtures")

#: Every fixture, so the coverage test can sweep them all without a hardcoded list.
FIXTURE_NAMES = (
    "charter-admit-run-terminal.jsonl",
    "charter-window-failed.jsonl",
    "cdp-leg.jsonl",
    "cdp-leg-e2e.jsonl",
    "parked-parent.jsonl",
    "gs2-dual-emitter.jsonl",
    "sdk-lifecycle-slice2.jsonl",
)


def fixture_path(name: str) -> str:
    """Return the absolute path to fixture ``name``."""
    return os.path.join(FIXTURE_DIR, name)


def load(name: str) -> JsonlEventSource:
    """Return a replay source over fixture ``name``."""
    return JsonlEventSource.from_path(fixture_path(name))


def replay(name: str, now_offset_ms: int = 0) -> tuple[Model, int]:
    """Fold fixture ``name`` and return ``(model, now_ms)``.

    ``now_ms`` defaults to the fixture's own high-water timestamp so that every
    age-derived field is reproducible; ``now_offset_ms`` advances it to let a test
    cross an idle threshold deliberately rather than by waiting.
    """
    source = load(name)
    model = Model()
    source.subscribe(model.apply)
    return model, source.max_ts() + now_offset_ms


@pytest.fixture(params=FIXTURE_NAMES)
def any_fixture(request) -> str:
    """Parametrise a test across every fixture."""
    return request.param
