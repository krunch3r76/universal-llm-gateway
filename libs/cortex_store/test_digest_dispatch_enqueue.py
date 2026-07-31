"""Hermetic tests for session-close digest enqueue-only dispatch."""

from __future__ import annotations

import threading
import time
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from cortex_store.digest_dispatch import dispatch_digest_background

_VALID_DIGEST = {
    "journal_entity_id": "document:journal",
    "entry_anchor": "2026-07-14#health",
    "entry_text": "Operator called the clinic.",
}


@pytest.fixture(autouse=True)
def _clear_hook_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CORTEX_DIGEST_CLOSE_HOOK", raising=False)


@pytest.mark.offline
def test_env_off_no_thread_no_op() -> None:
    with (
        patch("cortex_store.digest_dispatch.threading.Thread") as thread_cls,
        patch("cortex_store.dispatch_ops.ops_digest._digest_enqueue_phase1") as enqueue,
    ):
        dispatch_digest_background(_VALID_DIGEST, session_id="sess-1")
    thread_cls.assert_not_called()
    enqueue.assert_not_called()


@pytest.mark.offline
def test_env_on_missing_digest_keys_no_op(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORTEX_DIGEST_CLOSE_HOOK", "1")
    with (
        patch("cortex_store.digest_dispatch.threading.Thread") as thread_cls,
        patch("cortex_store.dispatch_ops.ops_digest._digest_enqueue_phase1") as enqueue,
    ):
        dispatch_digest_background({"journal_entity_id": "document:journal"}, session_id="s")
        dispatch_digest_background({"entry_anchor": "a"}, session_id="s")
        dispatch_digest_background("not-a-dict", session_id="s")  # type: ignore[arg-type]
    thread_cls.assert_not_called()
    enqueue.assert_not_called()


@pytest.mark.offline
def test_env_on_valid_digest_enqueues_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORTEX_DIGEST_CLOSE_HOOK", "1")
    started: list[tuple[Any, ...]] = []

    def _capture_start(self: threading.Thread) -> None:
        started.append(self._args)  # type: ignore[attr-defined]
        self._target(*self._args, **self._kwargs)  # type: ignore[attr-defined]

    with (
        patch("cortex_store.digest_dispatch.threading.Thread.start", _capture_start),
        patch("cortex_store.dispatch_ops.ops_digest._digest_enqueue_phase1") as enqueue,
        patch("cortex_store.digest_dispatch.digest_run") as digest_run,
    ):
        enqueue.return_value = {"status": "enqueued", "job_id": "job-close-1"}
        dispatch_digest_background(_VALID_DIGEST, session_id="sess-abc")

    assert len(started) == 1
    validated, session_id = started[0]
    assert session_id == "sess-abc"
    digest_run.assert_called_once()
    enqueue.assert_called_once_with(**validated)
    assert enqueue.call_args.kwargs == validated or enqueue.call_args.args == ()


@pytest.mark.offline
def test_close_hook_returns_quickly_without_sync_extract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CORTEX_DIGEST_CLOSE_HOOK", "1")

    def _slow_enqueue(**_: Any) -> dict[str, Any]:
        time.sleep(0.05)
        return {"status": "enqueued", "job_id": "job-fast"}

    with (
        patch(
            "cortex_store.dispatch_ops.ops_digest._digest_enqueue_phase1",
            side_effect=_slow_enqueue,
        ),
        patch("cortex_store.digest_dispatch.digest_run"),
    ):
        start = time.monotonic()
        dispatch_digest_background(_VALID_DIGEST, session_id="sess-fast")
        elapsed = time.monotonic() - start

    assert elapsed < 1.0


@pytest.mark.offline
def test_enqueue_exception_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORTEX_DIGEST_CLOSE_HOOK", "1")
    with patch(
        "cortex_store.digest_dispatch.threading.Thread",
        side_effect=RuntimeError("thread pool saturated"),
    ):
        dispatch_digest_background(_VALID_DIGEST, session_id="sess-err")


@pytest.mark.offline
def test_enqueue_failure_swallowed_in_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORTEX_DIGEST_CLOSE_HOOK", "1")

    def _run_immediately(
        target: Any, args: tuple[Any, ...] = (), kwargs: dict[str, Any] | None = None, **__: Any
    ) -> MagicMock:
        del kwargs
        target(*args)
        mock = MagicMock()
        mock.start.return_value = None
        return mock

    with (
        patch("cortex_store.digest_dispatch.threading.Thread", side_effect=_run_immediately),
        patch(
            "cortex_store.dispatch_ops.ops_digest._digest_enqueue_phase1",
            side_effect=RuntimeError("boom"),
        ),
        patch("cortex_store.digest_dispatch.digest_run"),
    ):
        dispatch_digest_background(_VALID_DIGEST, session_id="sess-thread")
