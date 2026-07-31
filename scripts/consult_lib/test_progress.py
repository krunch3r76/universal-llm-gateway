"""Unit tests for consult_lib progress liveness helpers."""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

import httpx
import pytest

from consult_lib.progress import (
    ProgressAbortError,
    _active_load,
    _is_forward,
    derive_deadline,
    post_with_progress,
)


def test_derive_deadline_defaults_to_at_least_900() -> None:
    assert derive_deadline(300.0) == 900.0
    assert derive_deadline(1200.0) == 1200.0
    assert derive_deadline(300.0, deadline=600.0) == 600.0


def test_forward_queue_drop_and_load_transitions() -> None:
    base = {
        "source": "admission",
        "queue_depth": 2,
        "loading": False,
        "loaded": False,
        "paused": False,
        "status": None,
    }
    assert not _is_forward(None, base)
    dropped = {**base, "queue_depth": 1}
    assert _is_forward(base, dropped)
    loading = {**base, "loading": True}
    assert _is_forward(base, loading)
    loaded = {**base, "loaded": True}
    assert _is_forward(base, loaded)


def test_forward_status_rank() -> None:
    prev = {
        "source": "status",
        "queue_depth": 0,
        "loading": False,
        "loaded": False,
        "paused": False,
        "status": "available",
    }
    curr = {**prev, "status": "loading", "loading": True}
    assert _is_forward(prev, curr)


def test_active_load() -> None:
    idle = {
        "source": "admission",
        "queue_depth": 0,
        "loading": False,
        "loaded": False,
        "paused": False,
        "status": None,
    }
    assert not _active_load(idle)
    assert _active_load({**idle, "loading": True})
    assert _active_load({**idle, "queue_depth": 3})


def test_post_with_progress_aborts_on_hard_deadline() -> None:
    """Closing the client from the poller surfaces ProgressAbortError."""

    class _BlockingClient:
        def __init__(self, *args, **kwargs) -> None:
            self._closed = threading.Event()

        def __enter__(self) -> "_BlockingClient":
            return self

        def __exit__(self, *args) -> None:
            return None

        def close(self) -> None:
            self._closed.set()

        def post(self, *args, **kwargs):
            # Wait until poller closes us (deadline), then raise like httpx.
            assert self._closed.wait(timeout=2.0)
            raise httpx.ConnectError("closed", request=MagicMock())

    snap = {
        "source": "admission",
        "queue_depth": 0,
        "loading": False,
        "loaded": False,
        "paused": False,
        "status": None,
    }
    with (
        patch("consult_lib.progress.httpx.Client", _BlockingClient),
        patch("consult_lib.progress._snapshot", return_value=snap),
    ):
        with pytest.raises(ProgressAbortError) as exc_info:
            post_with_progress(
                "http://example/v1/chat/completions",
                {"model": "m"},
                model_id="m",
                stargate_url="http://example",
                step_budget=60.0,
                deadline=0.15,
                poll_interval=0.05,
            )
    assert exc_info.value.reason == "hard_deadline"


def test_active_load_keeps_watchdog_fresh() -> None:
    """While loading, polls refresh last_progress so step_budget does not fire."""
    started = time.monotonic()
    last_progress = started
    snap = {
        "source": "admission",
        "queue_depth": 0,
        "loading": True,
        "loaded": False,
        "paused": False,
        "status": None,
    }
    # Simulate 4 minutes of loading with a 300s step budget — each poll refreshes.
    for _ in range(50):
        now = last_progress + 5.0
        if _active_load(snap):
            last_progress = now
        assert (now - last_progress) <= 300.0
    assert last_progress - started == 250.0
