"""§5.11 handler integration tests (#1–#6, #15, #21–#22, #24, +#25 staged, +#26 audit_incomplete)."""

from __future__ import annotations

import asyncio
import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from tools import _grokbuild_dispatch as dispatch_mod
from tools._grokbuild_runner import run_dispatch
from tools._grokbuild_test_support import (
    PROMPT,
    FakeProc,
    install_capture_post_state,
    install_grok_path,
    install_subprocess_exec,
    install_subprocess_run,
)
from tools.grokbuild import grokbuild


@pytest.mark.asyncio
async def test_read_only_happy_path(
    admission: str,
    event_log: list[tuple[str, dict[str, Any]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_capture_post_state(monkeypatch, status_post="", diff_stat="")
    install_subprocess_exec(monkeypatch)
    run_mock = AsyncMock(wraps=run_dispatch)
    monkeypatch.setattr(dispatch_mod, "run_dispatch", run_mock)

    out = await grokbuild("build", admission, PROMPT, mode="read_only")

    assert out["status"] == "completed"
    assert out["metadata"]["read_only_violation"] is False
    assert out["metadata"]["audit_incomplete"] is False
    assert out["metadata"]["sidecar_gaps"] == 0
    signals = [s for s, _ in event_log]
    assert "mcp.grokbuild.dispatch.called" in signals
    assert "mcp.grokbuild.dispatch.completed" in signals
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

    out = await grokbuild("build", admission, PROMPT, mode="edit")

    assert out["status"] == "completed"
    assert out["metadata"]["git_diff_stat"] == diff
    assert out["metadata"]["read_only_violation"] is False
    completed = next(p for s, p in event_log if s.endswith(".completed"))
    assert completed["git_diff_stat"] == diff


@pytest.mark.asyncio
async def test_edit_working_tree_dirty_rejection(
    git_repo: object,
    event_log: list[tuple[str, dict[str, Any]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cwd = str(git_repo)
    install_grok_path(monkeypatch)
    install_subprocess_run(monkeypatch, cwd=cwd, status_pre=" M dirty.txt\n")
    run_mock = AsyncMock()
    monkeypatch.setattr(dispatch_mod, "run_dispatch", run_mock)

    out = await grokbuild("build", cwd, PROMPT, mode="edit")

    assert out["status"] == "rejected"
    assert out["metadata"]["reason_code"] == "working_tree_dirty"
    assert out["metadata"]["reason"]
    assert any(p.get("reason_code") == "working_tree_dirty" for _, p in event_log)
    rejected = next(p for s, p in event_log if s.endswith(".rejected"))
    # Rejected event must carry correlation fields (per architecture-invariants
    # admission-phase payload contract; here mode/op/cwd/model travel on the
    # rejected event itself instead of joining via .called → dispatch_id).
    assert rejected["cwd"] == cwd
    assert rejected["mode"] == "edit"
    assert rejected["op"] == "build"
    run_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_read_only_dirty_admission_sets_audit_incomplete(
    git_repo: object,
    event_log: list[tuple[str, dict[str, Any]]],
    monkeypatch: pytest.MonkeyPatch,
    sidecar_root: object,
) -> None:
    """read_only admits a dirty tree; audit_incomplete=True, violation suppressed.

    Post-state mock returns a non-empty porcelain (the same dirty file plus
    whatever grok may have done). Without the dirty_admission gate, this
    would flag read_only_violation=True; the gate forces audit_incomplete
    and clears the violation flag because the verdict is indeterminate.
    """
    cwd = str(git_repo)
    install_grok_path(monkeypatch)
    install_subprocess_run(monkeypatch, cwd=cwd, status_pre=" M dirty.txt\n")
    install_capture_post_state(
        monkeypatch, status_post=" M dirty.txt\n", diff_stat=" dirty.txt | 1 +\n"
    )
    install_subprocess_exec(monkeypatch)

    out = await grokbuild("build", cwd, PROMPT, mode="read_only")

    assert out["status"] == "completed"
    assert out["metadata"]["audit_incomplete"] is True
    assert out["metadata"]["read_only_violation"] is False
    completed = next(p for s, p in event_log if s.endswith(".completed"))
    assert completed["audit_incomplete"] is True
    assert completed["read_only_violation"] is False


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

    out = await grokbuild("build", admission, PROMPT, mode="read_only")

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

    out = await grokbuild("build", admission, PROMPT, mode="read_only")

    assert out["metadata"]["read_only_violation"] is True


@pytest.mark.asyncio
async def test_read_only_staged_modification_violation(
    admission: str,
    event_log: list[tuple[str, dict[str, Any]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Audit must catch staged-only mutations.

    Porcelain ``M  tracked.txt`` (capital M in col 1, space in col 2 = staged
    modification) with empty ``git diff --stat`` (working tree == index after
    a hypothetical mid-dispatch ``git add``) is invisible to a predicate that
    only checks diff_stat + ``??`` untracked lines. The corrected predicate
    catches it via the full porcelain string.
    """
    install_capture_post_state(
        monkeypatch, status_post="M  tracked.txt\n", diff_stat=""
    )
    install_subprocess_exec(monkeypatch)

    out = await grokbuild("build", admission, PROMPT, mode="read_only")

    assert out["metadata"]["read_only_violation"] is True
    completed = next(p for s, p in event_log if s.endswith(".completed"))
    assert completed["read_only_violation"] is True


@pytest.mark.asyncio
async def test_audit_incomplete_propagated(
    admission: str,
    event_log: list[tuple[str, dict[str, Any]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A broken post-state read must surface as audit_incomplete, distinct
    from a clean repo. Filters must be able to distinguish 'verified clean'
    from 'audit failed, verdict unknown'.
    """
    install_capture_post_state(
        monkeypatch, status_post="", diff_stat="", audit_incomplete=True
    )
    install_subprocess_exec(monkeypatch)

    out = await grokbuild("build", admission, PROMPT, mode="read_only")

    assert out["status"] == "completed"
    assert out["metadata"]["audit_incomplete"] is True
    completed = next(p for s, p in event_log if s.endswith(".completed"))
    assert completed["audit_incomplete"] is True


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

    out = await grokbuild("build", admission, PROMPT, mode="edit")

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

    out = await grokbuild(
        "build", admission, PROMPT, mode="read_only", timeout_seconds=1
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

    out1 = await grokbuild("build", admission, PROMPT)
    out2 = await grokbuild("build", admission, PROMPT)

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
    monkeypatch.setattr(dispatch_mod, "run_dispatch", run_mock)

    out = await grokbuild("worktree", admission, PROMPT)  # type: ignore[arg-type]

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
    monkeypatch.setattr("tools._grokbuild_validator._SIDECAR_DIR", blocked)
    run_mock = AsyncMock()
    monkeypatch.setattr(dispatch_mod, "run_dispatch", run_mock)

    out = await grokbuild("build", admission, PROMPT)

    assert out["status"] == "rejected"
    assert any(p.get("reason_code") == "sidecar_unavailable" for _, p in event_log)
    blocked.mkdir.assert_called_once()
    run_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_dispatch_conflict_rejects_second_concurrent_call(
    admission: str,
    event_log: list[tuple[str, dict[str, Any]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A dispatch into a cwd that is already in flight rejects without
    spawning grok. Pre-populating the registry simulates the in-flight
    condition; verifies validator passes → registry rejects → no runner
    call.
    """
    from tools._grokbuild_registry import _reset_for_tests, try_acquire_cwd

    _reset_for_tests()
    # Pre-populate the registry as if a concurrent dispatch were already running.
    assert await try_acquire_cwd(admission, "uuid-inflight-9") is True

    run_mock = AsyncMock()
    monkeypatch.setattr(dispatch_mod, "run_dispatch", run_mock)

    out = await grokbuild("build", admission, PROMPT, mode="read_only")

    assert out["status"] == "rejected"
    assert out["metadata"]["reason_code"] == "dispatch_conflict"
    assert "already in flight" in out["metadata"]["reason"]
    # Recovery path: caller can fetch_result(conflicting_dispatch_id) without
    # sidecar grep.
    assert out["metadata"]["conflicting_dispatch_id"] == "uuid-inflight-9"
    rejected = next(p for s, p in event_log if s.endswith(".rejected"))
    assert rejected["reason_code"] == "dispatch_conflict"
    assert rejected["cwd"] == admission
    run_mock.assert_not_awaited()
    _reset_for_tests()


@pytest.mark.asyncio
async def test_dispatch_releases_cwd_after_completion(
    admission: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Successful dispatch must release the cwd so a subsequent dispatch
    into the same cwd succeeds (i.e. release_cwd ran in the finally)."""
    install_capture_post_state(monkeypatch, status_post="", diff_stat="")
    install_subprocess_exec(monkeypatch)

    out1 = await grokbuild("build", admission, PROMPT, mode="read_only")
    out2 = await grokbuild("build", admission, PROMPT, mode="read_only")

    assert out1["status"] == "completed"
    assert out2["status"] == "completed"
    assert out2["metadata"]["reason_code"] != "dispatch_conflict"


@pytest.mark.asyncio
async def test_failed_status_emits_failed_event(
    admission: str,
    event_log: list[tuple[str, dict[str, Any]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-zero exit_code produces status=failed and fires the .failed event
    factory + emit wrapper. The error field is the stderr tail truncated to
    200 chars.
    """
    install_capture_post_state(monkeypatch, status_post="", diff_stat="")
    install_subprocess_exec(
        monkeypatch,
        FakeProc(stdout=b"", stderr=b"grok crashed: traceback line\n", returncode=2),
    )

    out = await grokbuild("build", admission, PROMPT, mode="read_only")

    assert out["status"] == "failed"
    assert out["exit_code"] == 2
    failed = next(p for s, p in event_log if s.endswith(".failed"))
    assert failed["exit_code"] == 2
    assert "grok crashed" in failed["error"]
    assert len(failed["error"]) <= 200


@pytest.mark.asyncio
async def test_retired_op_dispatch_rejected_at_handler(
    admission: str,
    event_log: list[tuple[str, dict[str, Any]]],
) -> None:
    """op='dispatch' returns rejected envelope with reason_code=retired_op
    at the handler level — never reaches dispatch_op or validate_dispatch."""
    out = await grokbuild("dispatch", admission, PROMPT, mode="read_only")
    assert out["status"] == "rejected"
    assert out["metadata"]["reason_code"] == "retired_op"
    signals = [s for s, payload in event_log]
    assert "mcp.grokbuild.dispatch.rejected" in signals
    rejected_payload = next(
        p for s, p in event_log if s == "mcp.grokbuild.dispatch.rejected"
    )
    assert rejected_payload["op"] == "dispatch"


@pytest.mark.asyncio
async def test_retired_continue_recent_rejected(
    admission: str,
) -> None:
    """continue_recent=True yields retired_param reject."""
    out = await grokbuild(
        "build", admission, PROMPT, mode="read_only", continue_recent=True
    )
    assert out["status"] == "rejected"
    assert out["metadata"]["reason_code"] == "retired_param"


@pytest.mark.asyncio
async def test_resume_strict_without_session_id_rejected(
    admission: str,
) -> None:
    out = await grokbuild(
        "build", admission, PROMPT, mode="read_only", resume_strict=True
    )
    assert out["status"] == "rejected"
    assert out["metadata"]["reason_code"] == "bad_resume_strict_without_session_id"


@pytest.mark.asyncio
async def test_session_id_captured_from_streaming_json(
    admission: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_capture_post_state(monkeypatch, status_post="", diff_stat="")
    streaming_stdout = (
        b'{"phase":"start","sessionId":"sid-789"}\n'
        b'{"phase":"chunk","data":"hello"}\n'
        b'{"phase":"end","sessionId":"sid-999"}\n'
    )
    install_subprocess_exec(monkeypatch, FakeProc(stdout=streaming_stdout))
    out = await grokbuild("build", admission, PROMPT, mode="read_only")
    assert out["status"] == "completed"
    assert out["metadata"]["resolved_session_id"] == "sid-789"


@pytest.mark.asyncio
async def test_tier_quick_overlay_in_envelope_metadata(
    admission: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """tier='quick' resolves reasoning_effort=minimal, effort=low; envelope metadata echoes resolved values."""
    install_capture_post_state(monkeypatch, status_post="", diff_stat="")
    install_subprocess_exec(monkeypatch)
    out = await grokbuild("build", admission, PROMPT, mode="read_only", tier="quick")
    assert out["status"] == "completed"
    assert out["metadata"]["tier"] == "quick"
    assert out["metadata"]["reasoning_effort"] == "minimal"
    assert out["metadata"]["effort"] == "low"


@pytest.mark.asyncio
async def test_resume_strict_emits_dash_r(
    admission: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_capture_post_state(monkeypatch, status_post="", diff_stat="")
    captured: list[list[str]] = []

    async def _spy_spawn(*args: Any, **kwargs: Any) -> Any:
        captured.append(list(args))
        return FakeProc()

    monkeypatch.setattr("asyncio.create_subprocess_exec", _spy_spawn)
    await grokbuild(
        "build",
        admission,
        PROMPT,
        mode="read_only",
        session_id="abc-123",
        resume_strict=True,
    )
    assert "-r" in captured[0]
    assert captured[0][captured[0].index("-r") + 1] == "abc-123"
    assert "-s" not in captured[0]


@pytest.mark.asyncio
async def test_session_id_idempotent_emits_dash_s(
    admission: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_capture_post_state(monkeypatch, status_post="", diff_stat="")
    captured: list[list[str]] = []

    async def _spy_spawn(*args: Any, **kwargs: Any) -> Any:
        captured.append(list(args))
        return FakeProc()

    monkeypatch.setattr("asyncio.create_subprocess_exec", _spy_spawn)
    await grokbuild("build", admission, PROMPT, mode="read_only", session_id="abc-123")
    assert "-s" in captured[0]
    assert captured[0][captured[0].index("-s") + 1] == "abc-123"
    assert "-r" not in captured[0]


# ── caller-wins-over-tier (independent axis control) ────────────────────────


@pytest.mark.asyncio
async def test_caller_effort_overrides_tier_preset(
    admission: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Explicit effort wins over tier preset; reasoning_effort falls back to preset.

    tier='quick' preset: reasoning_effort=minimal, effort=low.
    Caller passes effort='max' → effort='max' in envelope, reasoning_effort=minimal.
    """
    install_capture_post_state(monkeypatch, status_post="", diff_stat="")
    install_subprocess_exec(monkeypatch)

    out = await grokbuild(
        "build", admission, PROMPT, mode="read_only", tier="quick", effort="max"
    )

    assert out["status"] == "completed"
    assert out["metadata"]["tier"] == "quick"
    assert out["metadata"]["effort"] == "max"  # caller wins
    assert out["metadata"]["reasoning_effort"] == "minimal"  # preset fallback


@pytest.mark.asyncio
async def test_caller_reasoning_effort_overrides_tier_preset(
    admission: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Explicit reasoning_effort wins over tier preset; effort falls back to preset.

    tier='quick' preset: reasoning_effort=minimal, effort=low.
    Caller passes reasoning_effort='xhigh' → reasoning_effort='xhigh', effort=low.
    """
    install_capture_post_state(monkeypatch, status_post="", diff_stat="")
    install_subprocess_exec(monkeypatch)

    out = await grokbuild(
        "build",
        admission,
        PROMPT,
        mode="read_only",
        tier="quick",
        reasoning_effort="xhigh",
    )

    assert out["status"] == "completed"
    assert out["metadata"]["tier"] == "quick"
    assert out["metadata"]["reasoning_effort"] == "xhigh"  # caller wins
    assert out["metadata"]["effort"] == "low"  # preset fallback


@pytest.mark.asyncio
async def test_caller_both_axes_override_tier_preset(
    admission: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both effort and reasoning_effort independently override tier preset.

    tier='quick' preset: reasoning_effort=minimal, effort=low.
    Caller overrides both axes → both caller values appear in the envelope.
    """
    install_capture_post_state(monkeypatch, status_post="", diff_stat="")
    install_subprocess_exec(monkeypatch)

    out = await grokbuild(
        "build",
        admission,
        PROMPT,
        mode="read_only",
        tier="quick",  # preset: reasoning_effort=minimal, effort=low
        effort="xhigh",  # override effort axis
        reasoning_effort="high",  # override reasoning_effort axis
    )

    assert out["status"] == "completed"
    assert out["metadata"]["tier"] == "quick"
    assert out["metadata"]["effort"] == "xhigh"
    assert out["metadata"]["reasoning_effort"] == "high"
