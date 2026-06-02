"""Shared fixtures and mocks for cursorbuild tests."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
import pytest

from cursorbuild.registry import _reset_for_tests
from cursorbuild.runner_types import RunnerSpec
from cursorbuild.validator import _reset_cursor_agent_cache_for_tests

CURSOR_BIN = "/usr/bin/cursor-agent"
PROMPT = "do the thing"


@pytest.fixture(autouse=True)
def _clear_registry() -> None:
    _reset_for_tests()


@pytest.fixture
def cursorbuild_sidecar_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "cursorbuild-sidecars"
    monkeypatch.setattr("cursorbuild.runner._SIDECAR_DIR", root)
    monkeypatch.setattr("cursorbuild.validator._SIDECAR_DIR", root)
    monkeypatch.setattr("cursorbuild.fetch_result._SIDECAR_DIR", root)
    monkeypatch.setattr("cursorbuild.home._SIDECAR_DIR", root, raising=False)
    return root


class FakeProc:
    def __init__(
        self,
        *,
        stdout: bytes = b'{"type":"system","subtype":"init","session_id":"sess-1"}\n',
        stderr: bytes = b"",
        returncode: int = 0,
        pid: int = 4242,
    ) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode
        self.pid = pid

    async def communicate(self) -> tuple[bytes, bytes]:
        return self.stdout, self.stderr

    async def wait(self) -> int:
        return self.returncode


def install_subprocess_exec(
    monkeypatch: pytest.MonkeyPatch, proc: FakeProc | None = None
) -> FakeProc:
    fake = proc or FakeProc()

    async def _exec(*_a: Any, **_k: Any) -> FakeProc:
        return fake

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _exec)
    return fake


def install_capture_post_state(
    monkeypatch: pytest.MonkeyPatch,
    *,
    status_post: str = "",
    diff_stat: str = "",
    audit_incomplete: bool = False,
) -> None:
    async def _capture(_cwd: str) -> tuple[str, str, bool]:
        return status_post, diff_stat, audit_incomplete

    monkeypatch.setattr("cursorbuild.runner.sidecar._capture_post_state", _capture)


def install_dispatch_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def _setup(
        dispatch_id: str,
        sidecar_dir: Path,
        *,
        real_home: str | None,
        mcp_enabled: bool,
    ) -> Path:
        del real_home, mcp_enabled
        home = sidecar_dir / f"{dispatch_id}-home"
        home.mkdir(parents=True, exist_ok=True)
        return home

    monkeypatch.setattr("cursorbuild.runner.setup_dispatch_home", _setup)


def runner_spec(**overrides: Any) -> RunnerSpec:
    base: dict[str, Any] = {
        "dispatch_id": "d-test",
        "cwd": "/tmp/ws",
        "prompt": PROMPT,
        "mode": "read_only",
        "cursor_agent_bin": CURSOR_BIN,
        "model": None,
        "system_context": None,
        "session_id": None,
        "timeout_seconds": 30,
        "tier": "default",
    }
    base.update(overrides)
    return RunnerSpec(**base)


def sidecar_lines(root: Path, dispatch_id: str) -> list[dict[str, Any]]:
    path = root / f"{dispatch_id}.ndjson"
    lines: list[dict[str, Any]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        if raw.strip():
            lines.append(json.loads(raw))
    return lines


@pytest.fixture
def cursor_agent_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "cursorbuild.validator.shutil.which",
        lambda name: CURSOR_BIN if name == "cursor-agent" else None,
    )
    _reset_cursor_agent_cache_for_tests()
