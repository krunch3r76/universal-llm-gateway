"""Runner and dispatch scaffold tests."""

from __future__ import annotations

from pathlib import Path
import pytest

from cursorbuild.dispatch import dispatch_op
from cursorbuild.runner import STDOUT_MAX, run_dispatch
from cursorbuild.test_support import (
    CURSOR_BIN,
    FakeProc,
    install_capture_post_state,
    install_dispatch_home,
    install_subprocess_exec,
    runner_spec,
    sidecar_lines,
)


@pytest.mark.asyncio
async def test_stdout_truncation(
    cursorbuild_sidecar_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cursorbuild_sidecar_root.mkdir(parents=True, exist_ok=True)
    big = b'{"type":"system","subtype":"init","session_id":"s"}\n' + b"x" * (
        STDOUT_MAX + 500
    )
    install_subprocess_exec(monkeypatch, FakeProc(stdout=big))
    install_capture_post_state(monkeypatch)
    install_dispatch_home(monkeypatch, cursorbuild_sidecar_root)

    spec = runner_spec(cwd=str(cursorbuild_sidecar_root))
    rr = await run_dispatch(spec)

    assert rr.truncated is True
    assert len(rr.stdout) <= STDOUT_MAX


@pytest.mark.asyncio
async def test_dispatch_rejects_bad_tier(
    cursorbuild_sidecar_root: Path, cursor_agent_on_path: None
) -> None:
    out = await dispatch_op(
        str(cursorbuild_sidecar_root),
        "hi",
        mode="read_only",
        system_context=None,
        model=None,
        session_id=None,
        tier="not-a-tier",
    )
    assert out["status"] == "rejected"
    assert out["metadata"]["reason_code"] == "bad_tier"


@pytest.mark.asyncio
async def test_sidecar_started_record(
    cursorbuild_sidecar_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_subprocess_exec(monkeypatch)
    install_capture_post_state(monkeypatch)
    install_dispatch_home(monkeypatch, cursorbuild_sidecar_root)
    spec = runner_spec(cwd=str(cursorbuild_sidecar_root), dispatch_id="scaffold-id")
    await run_dispatch(spec)
    lines = sidecar_lines(cursorbuild_sidecar_root, "scaffold-id")
    started = next(r for r in lines if r["phase"] == "started")
    assert started["mode"] == "read_only"
    assert started["argv"][0] == CURSOR_BIN
    assert "--workspace" in started["argv"]
