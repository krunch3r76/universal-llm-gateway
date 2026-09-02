"""Hermetic tests for session-close digest background dispatch."""

from __future__ import annotations

import threading
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
        patch("cortex_store.dispatch_ops.ops_digest._digest_enqueue_phase1") as op_digest,
    ):
        dispatch_digest_background(_VALID_DIGEST, session_id="sess-1")
    thread_cls.assert_not_called()
    op_digest.assert_not_called()


@pytest.mark.offline
def test_env_on_missing_digest_keys_no_op(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORTEX_DIGEST_CLOSE_HOOK", "1")
    with (
        patch("cortex_store.digest_dispatch.threading.Thread") as thread_cls,
        patch("cortex_store.dispatch_ops.ops_digest._digest_enqueue_phase1") as op_digest,
    ):
        dispatch_digest_background({"journal_entity_id": "document:journal"}, session_id="s")
        dispatch_digest_background({"entry_anchor": "a"}, session_id="s")
        dispatch_digest_background("not-a-dict", session_id="s")  # type: ignore[arg-type]
    thread_cls.assert_not_called()
    op_digest.assert_not_called()


@pytest.mark.offline
def test_env_on_valid_digest_invokes_op(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORTEX_DIGEST_CLOSE_HOOK", "1")
    started: list[tuple[Any, ...]] = []

    def _capture_start(self: threading.Thread) -> None:
        started.append(self._args)  # type: ignore[attr-defined]
        self._target(*self._args, **self._kwargs)  # type: ignore[attr-defined]

    with (
        patch("cortex_store.digest_dispatch.threading.Thread.start", _capture_start),
        patch("cortex_store.dispatch_ops.ops_digest._digest_enqueue_phase1") as op_digest,
        patch("cortex_store.digest_dispatch.digest_run") as digest_run,
    ):
        dispatch_digest_background(_VALID_DIGEST, session_id="sess-abc")

    assert len(started) == 1
    validated, session_id = started[0]
    assert session_id == "sess-abc"
    assert validated["journal_entity_id"] == _VALID_DIGEST["journal_entity_id"]
    digest_run.assert_called_once_with(
        journal_entity_id=_VALID_DIGEST["journal_entity_id"],
        entry_anchor=_VALID_DIGEST["entry_anchor"],
        session_id="sess-abc",
    )
    op_digest.assert_called_once_with(**validated)


@pytest.mark.offline
def test_enqueue_exception_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORTEX_DIGEST_CLOSE_HOOK", "1")
    with patch(
        "cortex_store.digest_dispatch.threading.Thread",
        side_effect=RuntimeError("thread pool saturated"),
    ):
        dispatch_digest_background(_VALID_DIGEST, session_id="sess-err")


@pytest.mark.offline
def test_op_exception_swallowed_in_thread(monkeypatch: pytest.MonkeyPatch) -> None:
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
        patch("cortex_store.dispatch_ops.ops_digest._digest_enqueue_phase1", side_effect=RuntimeError("boom")),
        patch("cortex_store.digest_dispatch.digest_run"),
    ):
        dispatch_digest_background(_VALID_DIGEST, session_id="sess-thread")


@pytest.mark.offline
def test_session_close_seam_dispatches_without_failing_close() -> None:
    with (
        patch(
            "cortex_store.dispatch_ops.ops_session_close.resolve_session_summary_md",
            return_value=("## Summary\n", None),
        ),
        patch(
            "cortex_store.dispatch_ops.ops_session_close._validate_session_close_args",
            return_value=None,
        ),
        patch(
            "cortex_store.dispatch_ops.ops_session_close._safe_run_audit",
            return_value={},
        ),
        patch(
            "cortex_store.dispatch_ops.ops_session_close._close_session_impl",
            return_value={"ok": True, "session_id": "sess-close"},
        ),
        patch(
            "cortex_store.dispatch_ops.ops_session_close._append_session_close_warnings",
        ),
        patch(
            "cortex_store.dispatch_ops.ops_session_close.promote_session_objectives",
            return_value=None,
        ),
        patch(
            "cortex_store.dispatch_ops.ops_session_close.dispatch_digest_background",
        ) as dispatch_mock,
    ):
        from cortex_store.dispatch_ops.ops_session_close import _op_session_close

        result = _op_session_close(
            session_id="sess-close",
            agent="test-agent",
            session_summary_md="## Summary\n",
            summary="short summary",
            digest=_VALID_DIGEST,
        )

    dispatch_mock.assert_called_once_with(_VALID_DIGEST, session_id="sess-close")
    assert result.get("ok") is True
    assert "error" not in result
