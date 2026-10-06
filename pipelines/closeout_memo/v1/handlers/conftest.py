"""Shared fixtures for closeout-memo handler tests."""

from __future__ import annotations

from typing import Any

import pytest

from . import admit, coalesce, deliver, fallback, sweep

_HANDLER_EMIT_MODULES = (admit, coalesce, deliver, fallback, sweep)


@pytest.fixture
def closeout_memo_event_log() -> list[tuple[str, dict[str, Any]]]:
    """In-memory closeout_memo event capture (filled by autouse stub)."""
    return []


@pytest.fixture
def real_event_sync_calls() -> list[int]:
    """Counts calls to the real Event Service sync emitter (must stay zero)."""
    return []


@pytest.fixture(autouse=True)
def stub_closeout_memo_emit(
    monkeypatch: pytest.MonkeyPatch,
    closeout_memo_event_log: list[tuple[str, dict[str, Any]]],
    real_event_sync_calls: list[int],
) -> None:
    """Prevent handler tests from writing closeout_memo domain events to the bus."""

    def _record(name: str, **payload: Any) -> None:
        closeout_memo_event_log.append((name, dict(payload)))

    def _forbidden_sync(*_args: Any, **_kwargs: Any) -> None:
        real_event_sync_calls.append(1)

    import closeout_memo.events as closeout_events

    monkeypatch.setattr(closeout_events, "emit_closeout_memo", _record)
    for mod in _HANDLER_EMIT_MODULES:
        monkeypatch.setattr(mod, "emit_closeout_memo", _record)
    monkeypatch.setattr(
        "scripts.model_manager.observation_event._emit_sync",
        _forbidden_sync,
    )
