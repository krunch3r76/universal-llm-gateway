"""§5.11 handler integration tests (#1–#6, #15, #21–#22, #24)."""

from __future__ import annotations

import asyncio
import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from tools import grok_build as grok_build_mod
from tools._grok_build_runner import run_dispatch
from tools._grok_build_test_support import (
    PROMPT,
    FakeProc,
    install_capture_post_state,
    install_grok_path,
    install_subprocess_exec,
    install_subprocess_run,
)
from tools.grok_build import grok_build


@pytest.mark.asyncio
async def test_read_only_happy_path(
    admission: str,
    event_log: list[tuple[str, dict[str, Any]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_capture_post_state(monkeypatch, status_post="", diff_stat="")
    install_subprocess_exec(monkeypatch)
    run_mock = AsyncMock(wraps=run_dispatch)
    monkeypatch.setattr(grok_build_mod, "run_dispatch", run_mock)

    out = await grok_build("dispatch", admission, PROMPT, mode="read_only")

    assert out["status"] == "completed"
    assert out["metadata"]["read_only_violation"] is False
    signals = [s for s, _ in event_log]
    assert "mcp.grok.build.dispatch.called" in signals
    assert "mcp.grok.build.dispatch.completed" in signals
    run_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_edit_mode_happy_path(
    admission: str,
    event_log: list[tuple[str, dict[str, Any]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    diff = " tracked.txt | 1 +\n"
    install_capture_post_state(
        monkeypatch, status_post=" M tracked.txt\n", diff_stat=diff
    )
    install_subprocess_exec(monkeypatch)

    out = await grok_build("dispatch", admission, PROMPT, mode="edit")

    assert out["status"] == "completed"
    assert out["metadata"]["git_diff_stat"] == diff
    assert out["metadata"]["read_only_violation"] is False
    completed = next(p for s, p in event_log if s.endswith(".completed"))
    assert completed["git_diff_stat"] == diff


@pytest.mark.asyncio
async def test_working_tree_dirty_rejection(
    git_repo: object,
    event_log: list[tuple[str, dict[str, Any]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cwd = str(git_repo)
    install_grok_path(monkeypatch)
    install_subprocess_run(monkeypatch, cwd=cwd, status_pre=" M dirty.txt\n")
    run_mock = AsyncMock()
    monkeypatch.setattr(grok_build_mod, "run_dispatch", run_mock)

    out = await grok_build("dispatch", cwd, PROMPT)

    assert out["status"] == "rejected"
    assert any(p.get("reason_code") == "working_tree_dirty" for _, p in event_log)
    run_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_read_only_violation_detected(
    admission: str,
    event_log: list[tuple[str, dict[str, Any]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    diff = " tracked.txt | 2 ++\n"
    install_capture_post_state(
        monkeypatch, status_post=" M tracked.txt\n", diff_stat=diff
    )
    install_subprocess_exec(monkeypatch)

    out = await grok_build("dispatch", admission, PROMPT, mode="read_only")

    assert out["metadata"]["read_only_violation"] is True
    assert out["metadata"]["git_diff_stat"] == diff
    completed = next(p for s, p in event_log if s.endswith(".completed"))
    assert completed["read_only_violation"] is True


@pytest.mark.asyncio
async def test_read_only_untracked_violation(
    admission: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_capture_post_state(monkeypatch, status_post="?? newfile\n", diff_stat="")
    install_subprocess_exec(monkeypatch)

    out = await grok_build("dispatch", admission, PROMPT, mode="read_only")

    assert out["metadata"]["read_only_violation"] is True


@pytest.mark.asyncio
async def test_edit_mode_captures_diff_into_event(
    admission: str,
    event_log: list[tuple[str, dict[str, Any]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    diff = " a.txt | 1 +\n b.txt | 2 ++\n c.txt | 3 +++\n"
    install_capture_post_state(
        monkeypatch,
        status_post=" M a.txt\n M b.txt\n M c.txt\n",
        diff_stat=diff,
    )
    install_subprocess_exec(monkeypatch)

    out = await grok_build("dispatch", admission, PROMPT, mode="edit")

    assert out["metadata"]["git_diff_stat"] == diff
    completed = next(p for s, p in event_log if s.endswith(".completed"))
    for name in ("a.txt", "b.txt", "c.txt"):
        assert name in completed["git_diff_stat"]


@pytest.mark.asyncio
async def test_timeout_kills_process_group_and_captures_post_state(
    admission: str,
    event_log: list[tuple[str, dict[str, Any]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    post_status = " M partial.txt\n"
    post_diff = " partial.txt | 1 +\n"
    install_capture_post_state(
        monkeypatch, status_post=post_status, diff_stat=post_diff
    )
    install_subprocess_exec(monkeypatch, FakeProc(hang=True))
    killpg = MagicMock()
    monkeypatch.setattr(os, "killpg", killpg)
    monkeypatch.setattr(os, "getpgid", lambda _pid: 99)

    async def _instant_timeout(coro: Any, timeout: float) -> Any:
        _ = timeout
        coro.close()
        raise TimeoutError

    monkeypatch.setattr(asyncio, "wait_for", _instant_timeout)

    out = await grok_build(
        "dispatch", admission, PROMPT, mode="read_only", timeout_seconds=1
    )

    assert out["status"] == "timeout"
    assert out["metadata"]["git_status_post"] == post_status
    assert out["metadata"]["git_diff_stat"] == post_diff
    killpg.assert_called()
    assert any(s.endswith(".timeout") for s, _ in event_log)


@pytest.mark.asyncio
async def test_dispatch_id_correlation(
    admission: str,
    event_log: list[tuple[str, dict[str, Any]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_capture_post_state(monkeypatch, status_post="", diff_stat="")
    install_subprocess_exec(monkeypatch)

    out1 = await grok_build("dispatch", admission, PROMPT)
    out2 = await grok_build("dispatch", admission, PROMPT)

    assert out1["dispatch_id"] != out2["dispatch_id"]
    called1 = next(
        p
        for s, p in event_log
        if s.endswith(".called") and p["dispatch_id"] == out1["dispatch_id"]
    )
    done1 = next(
        p
        for s, p in event_log
        if s.endswith(".completed") and p["dispatch_id"] == out1["dispatch_id"]
    )
    assert called1["dispatch_id"] == done1["dispatch_id"]


@pytest.mark.asyncio
async def test_unknown_op(
    admission: str,
    event_log: list[tuple[str, dict[str, Any]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_mock = AsyncMock()
    monkeypatch.setattr(grok_build_mod, "run_dispatch", run_mock)

    out = await grok_build("worktree", admission, PROMPT)  # type: ignore[arg-type]

    assert out["status"] == "rejected"
    assert any(p.get("reason_code") == "unknown_op" for _, p in event_log)
    run_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_sidecar_unavailable_rejects(
    admission: str,
    event_log: list[tuple[str, dict[str, Any]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_grok_path(monkeypatch)
    install_subprocess_run(monkeypatch, cwd=admission)
    blocked = MagicMock()
    blocked.mkdir.side_effect = PermissionError("denied")
    monkeypatch.setattr("tools._grok_build_validator._SIDECAR_DIR", blocked)
    run_mock = AsyncMock()
    monkeypatch.setattr(grok_build_mod, "run_dispatch", run_mock)

    out = await grok_build("dispatch", admission, PROMPT)

    assert out["status"] == "rejected"
    assert any(p.get("reason_code") == "sidecar_unavailable" for _, p in event_log)
    blocked.mkdir.assert_called_once()
    run_mock.assert_not_awaited()
