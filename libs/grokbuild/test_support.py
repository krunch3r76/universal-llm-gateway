"""Shared fixtures and mocks for grokbuild §5.11 test matrix."""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from grokbuild.registry import _reset_for_tests as _reset_registry
from grokbuild.runner import RunnerSpec
from grokbuild.validator import (
    _reset_grok_models_cache_for_tests,
    _resolve_grok_path,
)

GROK_BIN = "/usr/bin/grok"
PROMPT = "do the thing"


@pytest.fixture(autouse=True)
def _clear_in_flight_registry() -> None:
    """Reset the in-flight cwd registry between tests so residual state from a
    failed-mid-dispatch test doesn't poison dispatch_conflict assertions in
    later tests."""
    _reset_registry()


@pytest.fixture
def event_log(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    log: list[tuple[str, dict[str, Any]]] = []

    def _record(signal: str, **payload: Any) -> None:
        log.append((signal, payload))

    monkeypatch.setattr("grokbuild.events_core.record", _record)
    return log


@pytest.fixture
def sidecar_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "grokbuild"
    monkeypatch.setattr("grokbuild.fetch_result._SIDECAR_DIR", root)
    monkeypatch.setattr("grokbuild.validator._SIDECAR_DIR", root)
    monkeypatch.setattr("grokbuild.runner._SIDECAR_DIR", root)
    return root


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    cwd = tmp_path / "repo"
    cwd.mkdir()
    subprocess.run(["git", "init"], cwd=cwd, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=cwd,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=cwd,
        check=True,
        capture_output=True,
    )
    (cwd / "tracked.txt").write_text("base\n")
    subprocess.run(
        ["git", "add", "tracked.txt"], cwd=cwd, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=cwd,
        check=True,
        capture_output=True,
    )
    return cwd


@pytest.fixture
def admission(
    monkeypatch: pytest.MonkeyPatch, git_repo: Path, sidecar_root: Path
) -> str:
    cwd = str(git_repo)
    install_grok_path(monkeypatch)
    install_subprocess_run(monkeypatch, cwd=cwd, status_pre="")
    return cwd


def clear_validator_caches() -> None:
    _resolve_grok_path.cache_clear()
    _reset_grok_models_cache_for_tests()


def runner_spec(
    *,
    cwd: str,
    mode: str = "read_only",
    system_context: str | None = None,
    model: str | None = None,
    session_id: str | None = None,
    permission_mode: str = "plan",
    git_status_pre: str = "",
    dispatch_id: str = "test-dispatch-id",
    tier: str = "thorough",
    reasoning_effort: str | None = None,
    effort: str | None = None,
    check: bool = False,
    no_subagents: bool = False,
    disable_web_search: bool = False,
    max_turns: int | None = None,
    best_of_n: int | None = None,
    resume_strict: bool = False,
    timeout_seconds: int = 30,
) -> RunnerSpec:
    """Build a minimal RunnerSpec for runner-level tests.

    Defaults to ``tier='thorough'`` for compatibility with existing tests
    that don't care about overlay output. ``continue_recent`` is gone —
    Phase 4's validator rejects it before the runner can see it.

    ``model=None`` → no ``--model`` flag (grok-build capability lookup only).
    ``dispatch_op`` normally sets ``model`` from the tier preset before
    ``run_dispatch``. Pass a non-None model (e.g. ``model="grok-3"``) to test
    reasoning-flag emission paths.
    """
    return RunnerSpec(
        dispatch_id=dispatch_id,
        cwd=cwd,
        prompt=PROMPT,
        mode=mode,  # type: ignore[arg-type]
        permission_mode=permission_mode,
        system_context=system_context,
        model=model,
        session_id=session_id,
        timeout_seconds=timeout_seconds,
        grok_path=GROK_BIN,
        git_status_pre=git_status_pre,
        tier=tier,  # type: ignore[arg-type]
        reasoning_effort=reasoning_effort,
        effort=effort,
        check=check,
        no_subagents=no_subagents,
        disable_web_search=disable_web_search,
        max_turns=max_turns,
        best_of_n=best_of_n,
        resume_strict=resume_strict,
    )


def install_subprocess_run(
    monkeypatch: pytest.MonkeyPatch,
    *,
    cwd: str,
    status_pre: str = "",
    status_post: str = "",
    diff_stat: str = "",
    rev_parse_ok: bool = True,
    grok_models_rc: int = 0,
    post_state_calls: list[str] | None = None,
) -> None:
    post_calls = post_state_calls if post_state_calls is not None else []

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        _ = kwargs
        if cmd[0:2] == ["git", "-C"] and len(cmd) >= 4 and cmd[2] == cwd:
            sub = cmd[3:]
            if sub[0] == "rev-parse":
                if rev_parse_ok:
                    return subprocess.CompletedProcess(
                        cmd, 0, stdout=".git\n", stderr=""
                    )
                raise subprocess.CalledProcessError(1, cmd)
            if sub[0:2] == ["status", "--porcelain"]:
                use_post = bool(post_calls)
                if use_post:
                    post_calls.pop(0)
                out = status_post if use_post else status_pre
                return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")
            if sub[0:2] == ["diff", "--stat"]:
                return subprocess.CompletedProcess(cmd, 0, stdout=diff_stat, stderr="")
        if cmd == ["grok", "models"]:
            return subprocess.CompletedProcess(
                cmd, grok_models_rc, stdout="" if grok_models_rc else "ok\n", stderr=""
            )
        raise AssertionError(f"unexpected subprocess.run: {cmd!r}")

    monkeypatch.setattr(subprocess, "run", fake_run)


def install_capture_post_state(
    monkeypatch: pytest.MonkeyPatch,
    *,
    status_post: str,
    diff_stat: str,
    audit_incomplete: bool = False,
) -> None:
    async def _fake_capture(_cwd: str) -> tuple[str, str, bool]:
        return status_post, diff_stat, audit_incomplete

    monkeypatch.setattr(
        "grokbuild.runner._capture_post_state",
        _fake_capture,
    )


class FakeProc:
    def __init__(
        self,
        *,
        stdout: bytes = b'{"ok":true}',
        stderr: bytes = b"",
        returncode: int = 0,
        pid: int = 4242,
        hang: bool = False,
    ) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode
        self.pid = pid
        self._hang = hang

    async def communicate(self) -> tuple[bytes, bytes]:
        if self._hang:
            await asyncio.sleep(3600)
        return self.stdout, self.stderr

    async def wait(self) -> int:
        return self.returncode


def install_grok_path(monkeypatch: pytest.MonkeyPatch) -> None:
    clear_validator_caches()
    monkeypatch.setattr(
        "grokbuild.validator.shutil.which",
        lambda name: GROK_BIN if name == "grok" else None,
    )


def install_subprocess_exec(
    monkeypatch: pytest.MonkeyPatch, proc: FakeProc | None = None
) -> None:
    monkeypatch.setattr(
        asyncio,
        "create_subprocess_exec",
        AsyncMock(return_value=proc or FakeProc()),
    )


def sidecar_lines(sidecar_root: Path, dispatch_id: str) -> list[dict[str, Any]]:
    path = sidecar_root / f"{dispatch_id}.ndjson"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
