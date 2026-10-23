"""Hermetic tests for box isolated Chrome session start."""

from __future__ import annotations

import json
import stat
import threading
from pathlib import Path
from typing import Any

import pytest

from box_isolated_chrome.session import (
    DISPLAY_BAND_END,
    DISPLAY_BAND_START,
    FORBIDDEN_PROFILE,
    POOL_DISPLAYS,
    SessionError,
    allocate_display,
    claim_display,
    display_band,
    release_display,
    reserve_display,
    start_session,
    validate_profile_dir,
    wait_for_x11_socket,
)


@pytest.mark.offline
def test_pool_displays_never_in_band() -> None:
    assert POOL_DISPLAYS.isdisjoint(set(display_band()))


@pytest.mark.offline
def test_shared_profile_path_refused(tmp_path: Path) -> None:
    forbidden = tmp_path / "chrome-profile"
    forbidden.mkdir()
    with pytest.raises(SessionError, match="forbidden"):
        validate_profile_dir(forbidden, forbidden=forbidden)


@pytest.mark.offline
def test_shared_profile_symlink_refused(tmp_path: Path) -> None:
    forbidden = tmp_path / "chrome-profile"
    forbidden.mkdir()
    link = tmp_path / "sessions" / "evil"
    link.parent.mkdir()
    link.symlink_to(forbidden)
    with pytest.raises(SessionError, match="forbidden"):
        validate_profile_dir(link, forbidden=forbidden)


@pytest.mark.offline
def test_display_band_exhausted(tmp_path: Path) -> None:
    state_path = tmp_path / "state" / "sessions.json"
    displays = {str(d): {"session_id": "x", "profile_dir": "/tmp/p"} for d in display_band()}
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({"displays": displays}), encoding="utf-8")
    with pytest.raises(SessionError, match="display_band_exhausted"):
        allocate_display(state_path)


@pytest.mark.offline
def test_concurrent_alloc_distinct_displays(tmp_path: Path) -> None:
    state_path = tmp_path / "sessions.json"
    results: list[int] = []
    lock = threading.Lock()

    def worker() -> None:
        d = reserve_display(state_path, f"s-{threading.get_ident()}", tmp_path / f"p-{threading.get_ident()}")
        with lock:
            results.append(d)

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) == len(set(results))
    assert all(DISPLAY_BAND_START <= d <= DISPLAY_BAND_END for d in results)


class _FakeProc:
    def __init__(
        self,
        args: list[str],
        *,
        exit_code: int | None = None,
        registry: dict[str, Any] | None = None,
    ) -> None:
        self.args = args
        self._exit_code = exit_code
        self.terminated = False
        if registry is not None:
            exe = Path(args[0]).name
            registry.setdefault("spawn_order", []).append(exe)
            registry.setdefault("procs", []).append(self)

    def poll(self) -> int | None:
        return self._exit_code

    def wait(self, timeout: float | None = None) -> int:
        return self._exit_code or 0

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.terminated = True


def _fake_bin_dir(tmp_path: Path, names: list[str]) -> Path:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name in names:
        path = bindir / name
        path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return bindir


@pytest.mark.offline
def test_xvfb_before_chrome_spawn_order(tmp_path: Path) -> None:
    bindir = _fake_bin_dir(tmp_path, ["Xvfb", "x11vnc", "google-chrome"])
    state_dir = tmp_path / "state"
    sessions_root = tmp_path / "sessions"
    registry: dict[str, Any] = {"spawn_order": []}

    def popen_factory(args: list[str], **kwargs: Any) -> _FakeProc:
        return _FakeProc(list(args), registry=registry)

    def socket_exists(path: Path) -> bool:
        return True

    receipt = start_session(
        state_dir=state_dir,
        sessions_root=sessions_root,
        env={"PATH": str(bindir), "BOX_CHROME_BIN": str(bindir / "google-chrome")},
        popen=popen_factory,
        socket_exists=socket_exists,
    )
    assert registry["spawn_order"] == ["Xvfb", "x11vnc", "google-chrome"]
    assert receipt.display == DISPLAY_BAND_START
    assert receipt.profile_dir == sessions_root / receipt.session_id
    assert receipt.cdp_port == 9400
    assert receipt.live_view == f"vnc://127.0.0.1/{5900 + receipt.display}"
    payload = receipt.to_json_dict()
    assert payload["display"] == f":{receipt.display}"
    assert FORBIDDEN_PROFILE.name not in payload["profile_dir"]


@pytest.mark.offline
def test_missing_xvfb_binary(tmp_path: Path) -> None:
    bindir = _fake_bin_dir(tmp_path, ["x11vnc", "google-chrome"])
    with pytest.raises(SessionError, match="missing binary: Xvfb"):
        start_session(
            state_dir=tmp_path / "state",
            sessions_root=tmp_path / "sessions",
            env={"PATH": str(bindir), "BOX_CHROME_BIN": str(bindir / "google-chrome")},
            popen=lambda *a, **k: _FakeProc(list(a[0])),
            socket_exists=lambda p: True,
        )


@pytest.mark.offline
def test_chrome_fail_rollback_releases_display(tmp_path: Path) -> None:
    bindir = _fake_bin_dir(tmp_path, ["Xvfb", "x11vnc", "google-chrome"])
    state_dir = tmp_path / "state"
    sessions_root = tmp_path / "sessions"
    state_path = state_dir / "sessions.json"
    procs: list[_FakeProc] = []

    def popen_factory(args: list[str], **kwargs: Any) -> _FakeProc:
        name = Path(args[0]).name
        code = 1 if name == "google-chrome" else None
        proc = _FakeProc(list(args), exit_code=code)
        procs.append(proc)
        return proc

    with pytest.raises(SessionError, match="Chrome exited"):
        start_session(
            state_dir=state_dir,
            sessions_root=sessions_root,
            env={"PATH": str(bindir), "BOX_CHROME_BIN": str(bindir / "google-chrome")},
            popen=popen_factory,
            socket_exists=lambda p: True,
        )

    xvfb_procs = [p for p in procs if Path(p.args[0]).name == "Xvfb"]
    assert xvfb_procs and xvfb_procs[0].terminated
    data = json.loads(state_path.read_text(encoding="utf-8"))
    assert data["displays"] == {}
    assert not any(sessions_root.iterdir()) if sessions_root.is_dir() else True


@pytest.mark.offline
def test_xvfb_socket_wait_before_chrome(tmp_path: Path) -> None:
    bindir = _fake_bin_dir(tmp_path, ["Xvfb", "x11vnc", "google-chrome"])
    state_dir = tmp_path / "state"
    sessions_root = tmp_path / "sessions"
    seen: list[str] = []

    def popen_factory(args: list[str], **kwargs: Any) -> _FakeProc:
        seen.append(Path(args[0]).name)
        return _FakeProc(list(args))

    with pytest.raises(SessionError, match="X11 socket missing"):
        start_session(
            state_dir=state_dir,
            sessions_root=sessions_root,
            env={"PATH": str(bindir), "BOX_CHROME_BIN": str(bindir / "google-chrome")},
            popen=popen_factory,
            socket_exists=lambda p: False,
        )
    assert seen == ["Xvfb"]
    assert "google-chrome" not in seen


@pytest.mark.offline
def test_wait_for_x11_socket_times_out() -> None:
    with pytest.raises(SessionError, match="X11 socket missing"):
        wait_for_x11_socket(200, timeout_s=0.01, poll_interval_s=0.001, socket_exists=lambda p: False)


@pytest.mark.offline
def test_release_display(tmp_path: Path) -> None:
    state_path = tmp_path / "sessions.json"
    claim_display(state_path, 200, "abc", tmp_path / "p")
    release_display(state_path, 200)
    data = json.loads(state_path.read_text(encoding="utf-8"))
    assert "200" not in data["displays"]


@pytest.mark.offline
def test_receipt_display_in_band() -> None:
    for display in range(DISPLAY_BAND_START, DISPLAY_BAND_END + 1):
        port = 9400 + (display - DISPLAY_BAND_START)
        assert 9400 <= port <= 9419
