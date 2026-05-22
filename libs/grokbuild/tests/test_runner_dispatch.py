"""§5.11 runner dispatch/sidecar tests (#23, capture_post_state, sidecar integrity)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from grokbuild.runner import (
    STDOUT_MAX,
    run_dispatch,
)
from grokbuild.test_support import (
    FakeProc,
    install_capture_post_state,
    install_subprocess_exec,
    runner_spec,
    sidecar_lines,
)


@pytest.mark.asyncio
async def test_stdout_truncation(
    admission: str, sidecar_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sidecar_root.mkdir(parents=True, exist_ok=True)
    big = b"x" * (STDOUT_MAX + 1000)
    install_subprocess_exec(monkeypatch, FakeProc(stdout=big))
    install_capture_post_state(monkeypatch, status_post="", diff_stat="")

    spec = runner_spec(cwd=admission)
    rr = await run_dispatch(spec)

    assert rr.truncated is True
    assert len(rr.stdout) <= STDOUT_MAX
    lines = sidecar_lines(sidecar_root, spec.dispatch_id)
    # In streaming-json mode each line is capped at SIDECAR_STDOUT_LINE_MAX.
    # A single oversized line produces stdout_chunk_truncated, not stdout_chunk.
    from grokbuild.runner import SIDECAR_STDOUT_LINE_MAX

    chunk = next(
        r for r in lines if r.get("phase") in ("stdout_chunk", "stdout_chunk_truncated")
    )
    assert chunk.get("kept", len(chunk.get("data", ""))) >= SIDECAR_STDOUT_LINE_MAX


@pytest.mark.asyncio
async def test_sidecar_records_git_audit_fields(
    admission: str, sidecar_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pre = ""
    post = " M tracked.txt\n"
    diff = " tracked.txt | 1 +\n"
    install_capture_post_state(monkeypatch, status_post=post, diff_stat=diff)
    install_subprocess_exec(monkeypatch)
    sidecar_root.mkdir(parents=True, exist_ok=True)
    spec = runner_spec(
        cwd=admission, git_status_pre=pre, dispatch_id="audit-sidecar-id"
    )
    await run_dispatch(spec)

    lines = sidecar_lines(sidecar_root, "audit-sidecar-id")
    started = next(r for r in lines if r["phase"] == "started")
    exit_line = next(r for r in lines if r["phase"] == "exit")
    assert started["cwd"] == admission
    assert started["mode"] == "read_only"
    assert started["permission_mode"] == "plan"
    assert started["output_format"] == "streaming-json"
    assert started["git_status_pre"] == pre
    assert started["dirty_admission"] is False
    assert exit_line["status"] == "completed"
    assert exit_line["git_status_post"] == post
    assert exit_line["git_diff_stat"] == diff
    assert exit_line["sidecar_gaps"] == 0


@pytest.mark.asyncio
async def test_capture_post_state_clean_repo(git_repo: Path) -> None:
    """Real _capture_post_state on a clean git repo returns ('', '', False)."""
    from grokbuild.runner import _capture_post_state

    status, diff, incomplete = await _capture_post_state(str(git_repo))
    assert status == ""
    assert diff == ""
    assert incomplete is False


@pytest.mark.asyncio
async def test_capture_post_state_dirty_repo(git_repo: Path) -> None:
    """Real _capture_post_state on a dirty repo reports porcelain + diff stats
    and audit_incomplete=False. Covers the status.strip() True branch +
    diff_proc invocation path.
    """
    from grokbuild.runner import _capture_post_state

    (git_repo / "tracked.txt").write_text("mutated\n")
    (git_repo / "new_untracked.txt").write_text("hi\n")

    status, diff, incomplete = await _capture_post_state(str(git_repo))
    assert incomplete is False
    assert "tracked.txt" in status
    assert "new_untracked.txt" in status
    assert "tracked.txt" in diff


@pytest.mark.asyncio
async def test_capture_post_state_unreachable(tmp_path: Path) -> None:
    """When cwd is not a git repo, git status --porcelain exits non-zero
    (CalledProcessError) — audit_incomplete=True."""
    from grokbuild.runner import _capture_post_state

    bogus = tmp_path / "not-a-repo"
    bogus.mkdir()

    status, diff, incomplete = await _capture_post_state(str(bogus))
    assert incomplete is True
    assert status == ""
    assert diff == ""


@pytest.mark.asyncio
async def test_streaming_json_chunks_per_line(
    admission: str, sidecar_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """output_format='streaming-json' emits one stdout_chunk sidecar record
    per output line, distinct from the json case which writes a single chunk.
    """
    install_capture_post_state(monkeypatch, status_post="", diff_stat="")
    multi_line = b'{"event":1}\n{"event":2}\n{"event":3}'
    install_subprocess_exec(monkeypatch, FakeProc(stdout=multi_line))
    sidecar_root.mkdir(parents=True, exist_ok=True)
    spec = runner_spec(cwd=admission, dispatch_id="stream-id")

    await run_dispatch(spec)

    lines = sidecar_lines(sidecar_root, "stream-id")
    chunks = [r for r in lines if r.get("phase") == "stdout_chunk"]
    assert len(chunks) == 3
    assert chunks[0]["data"] == '{"event":1}'
    assert chunks[2]["data"] == '{"event":3}'


@pytest.mark.asyncio
async def test_stderr_chunk_written_to_sidecar(
    admission: str, sidecar_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Non-empty stderr produces a stderr_chunk sidecar record."""
    install_capture_post_state(monkeypatch, status_post="", diff_stat="")
    install_subprocess_exec(
        monkeypatch, FakeProc(stdout=b'{"ok":true}', stderr=b"warning text\n")
    )
    sidecar_root.mkdir(parents=True, exist_ok=True)
    spec = runner_spec(cwd=admission, dispatch_id="stderr-id")

    await run_dispatch(spec)

    lines = sidecar_lines(sidecar_root, "stderr-id")
    stderr_chunks = [r for r in lines if r.get("phase") == "stderr_chunk"]
    assert len(stderr_chunks) == 1
    assert "warning text" in stderr_chunks[0]["data"]


@pytest.mark.asyncio
async def test_started_sidecar_oserror_returns_audit_incomplete(
    admission: str, sidecar_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the 'started' sidecar write raises OSError before the subprocess
    spawn, run_dispatch returns status=failed with audit_incomplete=True and
    error='sidecar_write_failed: ...'. No subprocess is launched.
    """
    from unittest.mock import AsyncMock

    def boom(path: str, record: dict[str, object]) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("grokbuild.runner._append_sidecar", boom)
    exec_mock = AsyncMock()
    monkeypatch.setattr("asyncio.create_subprocess_exec", exec_mock)

    spec = runner_spec(cwd=admission, dispatch_id="started-fail-id")
    rr = await run_dispatch(spec)

    assert rr.status == "failed"
    assert rr.audit_incomplete is True
    assert rr.sidecar_path is None
    assert rr.error.startswith("sidecar_write_failed")
    exec_mock.assert_not_called()


@pytest.mark.asyncio
async def test_try_append_sidecar_increments_gaps_on_chunk_oserror(
    admission: str, sidecar_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sidecar chunk/exit writes that fail with OSError increment sidecar_gaps
    instead of being silently swallowed. The 'started' write succeeds (so
    run_dispatch proceeds); subsequent writes raise.
    """
    install_capture_post_state(monkeypatch, status_post="", diff_stat="")
    install_subprocess_exec(monkeypatch)
    sidecar_root.mkdir(parents=True, exist_ok=True)

    real_append = None
    call_count = [0]

    def selective_boom(path: str, record: dict[str, object]) -> None:
        call_count[0] += 1
        if call_count[0] == 1:
            # Let the 'started' write through to the real implementation.
            real_append(path, record)
            return
        raise OSError("disk full mid-dispatch")

    import grokbuild.runner as runner_mod
    import grokbuild.runner_sidecar as sidecar_mod

    real_append = runner_mod._append_sidecar  # type: ignore[assignment]
    # Patch in both runner (direct call from run_dispatch) and runner_sidecar
    # (_try_append_sidecar reads _append_sidecar from its own module namespace).
    monkeypatch.setattr(runner_mod, "_append_sidecar", selective_boom)
    monkeypatch.setattr(sidecar_mod, "_append_sidecar", selective_boom)

    spec = runner_spec(cwd=admission, dispatch_id="gaps-id")
    rr = await run_dispatch(spec)

    assert rr.status == "completed"
    assert rr.sidecar_gaps >= 2  # stdout_chunk + exit at minimum


@pytest.mark.asyncio
async def test_spawn_failed_returns_failed_envelope(
    sidecar_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sidecar_root.mkdir(parents=True, exist_ok=True)

    async def _raise(*_a: Any, **_k: Any) -> Any:
        raise FileNotFoundError("/usr/bin/grok not found")

    monkeypatch.setattr("asyncio.create_subprocess_exec", _raise)
    spec = runner_spec(cwd="/tmp")
    rr = await run_dispatch(spec)
    assert rr.status == "failed"
    assert rr.reason_code == "spawn_failed"
    assert rr.audit_incomplete is True
    assert rr.exit_code is None


@pytest.mark.asyncio
async def test_sidecar_stdout_chunk_truncation(
    sidecar_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from grokbuild.runner import SIDECAR_STDOUT_LINE_MAX

    big_line = b"x" * (SIDECAR_STDOUT_LINE_MAX + 100)
    install_capture_post_state(monkeypatch, status_post="", diff_stat="")
    install_subprocess_exec(monkeypatch, FakeProc(stdout=big_line))
    sidecar_root.mkdir(parents=True, exist_ok=True)
    spec = runner_spec(cwd="/tmp")
    rr = await run_dispatch(spec)
    assert rr.status == "completed"

    import json as _json

    sidecar_file = sidecar_root / f"{spec.dispatch_id}.ndjson"
    if sidecar_file.exists():
        lines = [
            _json.loads(ln)
            for ln in sidecar_file.read_text().splitlines()
            if ln.strip()
        ]
        truncated = [r for r in lines if r.get("phase") == "stdout_chunk_truncated"]
        assert truncated, (
            f"expected stdout_chunk_truncated record; phases: {[r.get('phase') for r in lines]}"
        )
        assert truncated[0]["kept"] == SIDECAR_STDOUT_LINE_MAX
