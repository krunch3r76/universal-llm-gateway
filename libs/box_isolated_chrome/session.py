"""Start an isolated Chrome session on a dedicated Xvfb display (:200–:219).

Each session gets its own profile under ``/home/box/chrome-sessions/<uuid>/``,
never the shared ``/home/box/chrome-profile``. Display allocation is flock-
serialised so concurrent ``start`` calls receive distinct displays and profiles.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from durable_io.atomic import path_flock

DISPLAY_BAND_START = 200
DISPLAY_BAND_END = 219
POOL_DISPLAYS = frozenset({1, 64, 91})

FORBIDDEN_PROFILE = Path("/home/box/chrome-profile")
DEFAULT_SESSIONS_ROOT = Path("/home/box/chrome-sessions")
DEFAULT_STATE_DIR = Path("/home/box/.gateway/box-isolated-chrome")

XVFB_BIN = "Xvfb"
X11VNC_BIN = "x11vnc"
DEFAULT_CHROME_BIN = "google-chrome"

SOCKET_WAIT_TIMEOUT_S = 15.0
SOCKET_POLL_INTERVAL_S = 0.05
CHROME_FAIL_GRACE_S = 0.5


class SessionError(Exception):
    """Non-recoverable session start failure (maps to non-zero CLI exit)."""

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class SessionReceipt:
    """Successful ``start`` payload."""

    session_id: str
    display: int
    profile_dir: Path
    cdp_port: int
    live_view: str

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "display": f":{self.display}",
            "profile_dir": str(self.profile_dir),
            "cdp_port": self.cdp_port,
            "live_view": self.live_view,
        }


class PopenLike(Protocol):
    args: Sequence[str]

    def poll(self) -> int | None: ...

    def wait(self, timeout: float | None = ...) -> int: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...


PopenFactory = Callable[..., PopenLike]


def display_band() -> range:
    """Inclusive display numbers permitted for isolated sessions."""
    return range(DISPLAY_BAND_START, DISPLAY_BAND_END + 1)


def cdp_port_for_display(display: int) -> int:
    if not DISPLAY_BAND_START <= display <= DISPLAY_BAND_END:
        msg = f"display {display} outside band :{DISPLAY_BAND_START}-:{DISPLAY_BAND_END}"
        raise ValueError(msg)
    return 9400 + (display - DISPLAY_BAND_START)


def live_view_for_display(display: int) -> str:
    return f"vnc://127.0.0.1/{5900 + display}"


def x11_socket_path(display: int) -> Path:
    return Path(f"/tmp/.X11-unix/X{display}")


def resolve_chrome_bin(env: Mapping[str, str] | None = None) -> str:
    mapping = env if env is not None else os.environ
    override = mapping.get("BOX_CHROME_BIN")
    if override:
        if not Path(override).is_file():
            raise SessionError(f"missing binary: {DEFAULT_CHROME_BIN}", code="missing_binary")
        return override
    path = mapping.get("PATH")
    found = shutil.which(DEFAULT_CHROME_BIN, path=path)
    if not found:
        raise SessionError(f"missing binary: {DEFAULT_CHROME_BIN}", code="missing_binary")
    return found


def validate_profile_dir(profile_dir: Path, *, forbidden: Path = FORBIDDEN_PROFILE) -> None:
    """Refuse the shared profile path and symlinks that resolve to it."""
    resolved = profile_dir.resolve()
    forbidden_resolved = forbidden.resolve()
    if resolved == forbidden_resolved:
        raise SessionError(
            f"forbidden profile_dir {profile_dir} (shared chrome-profile)",
            code="forbidden_profile",
        )
    if profile_dir.is_symlink():
        target = profile_dir.resolve()
        if target == forbidden_resolved:
            raise SessionError(
                f"forbidden profile_dir symlink to {forbidden}",
                code="forbidden_profile",
            )


def _read_state(state_path: Path) -> dict[str, Any]:
    if not state_path.is_file():
        return {"displays": {}}
    raw = state_path.read_text(encoding="utf-8")
    if not raw.strip():
        return {"displays": {}}
    data = json.loads(raw)
    if "displays" not in data or not isinstance(data["displays"], dict):
        return {"displays": {}}
    return data


def _write_state(state_path: Path, data: dict[str, Any]) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def allocate_display(state_path: Path) -> int:
    """Pick the lowest free display in :200–:219 without reserving (tests only)."""
    with path_flock(state_path):
        data = _read_state(state_path)
        in_use = {int(k) for k in data["displays"]}
        for display in display_band():
            if display in POOL_DISPLAYS:
                continue
            if display not in in_use:
                return display
    raise SessionError("display_band_exhausted", code="display_band_exhausted")


def reserve_display(
    state_path: Path,
    session_id: str,
    profile_dir: Path,
) -> int:
    """Atomically allocate and claim the lowest free display in the band."""
    with path_flock(state_path):
        data = _read_state(state_path)
        in_use = {int(k) for k in data["displays"]}
        for display in display_band():
            if display in POOL_DISPLAYS:
                continue
            if display not in in_use:
                data["displays"][str(display)] = {
                    "session_id": session_id,
                    "profile_dir": str(profile_dir),
                }
                _write_state(state_path, data)
                return display
    raise SessionError("display_band_exhausted", code="display_band_exhausted")


def claim_display(state_path: Path, display: int, session_id: str, profile_dir: Path) -> None:
    with path_flock(state_path):
        data = _read_state(state_path)
        key = str(display)
        if key in data["displays"]:
            raise SessionError(f"display :{display} already claimed", code="display_in_use")
        data["displays"][key] = {
            "session_id": session_id,
            "profile_dir": str(profile_dir),
        }
        _write_state(state_path, data)


def release_display(state_path: Path, display: int) -> None:
    with path_flock(state_path):
        data = _read_state(state_path)
        data["displays"].pop(str(display), None)
        _write_state(state_path, data)


def wait_for_x11_socket(
    display: int,
    *,
    timeout_s: float = SOCKET_WAIT_TIMEOUT_S,
    poll_interval_s: float = SOCKET_POLL_INTERVAL_S,
    socket_exists: Callable[[Path], bool] | None = None,
) -> None:
    exists = socket_exists or (lambda p: p.exists())
    deadline = time.monotonic() + timeout_s
    sock = x11_socket_path(display)
    while time.monotonic() < deadline:
        if exists(sock):
            return
        time.sleep(poll_interval_s)
    raise SessionError(f"X11 socket missing for :{display} after {timeout_s}s", code="xvfb_timeout")


def _require_binary(name: str, resolved: str | None, *, path: str | None) -> str:
    if resolved:
        return resolved
    found = shutil.which(name, path=path)
    if not found:
        raise SessionError(f"missing binary: {name}", code="missing_binary")
    return found


def start_session(
    *,
    state_dir: Path | None = None,
    sessions_root: Path | None = None,
    env: Mapping[str, str] | None = None,
    popen: PopenFactory | None = None,
    socket_exists: Callable[[Path], bool] | None = None,
) -> SessionReceipt:
    """Allocate display band slot, start Xvfb → x11vnc → Chrome, return receipt JSON fields."""
    state_dir = state_dir or DEFAULT_STATE_DIR
    sessions_root = sessions_root or DEFAULT_SESSIONS_ROOT
    runtime_env = dict(os.environ if env is None else env)
    popen_factory = popen or subprocess.Popen

    state_path = state_dir / "sessions.json"

    session_id = str(uuid.uuid4())
    profile_dir = sessions_root / session_id
    validate_profile_dir(profile_dir)
    profile_dir.mkdir(parents=True, exist_ok=False)

    path_env = runtime_env.get("PATH")
    xvfb_path = shutil.which(XVFB_BIN, path=path_env)
    x11vnc_path = shutil.which(X11VNC_BIN, path=path_env)
    _require_binary(XVFB_BIN, xvfb_path, path=path_env)
    _require_binary(X11VNC_BIN, x11vnc_path, path=path_env)
    chrome_bin = resolve_chrome_bin(runtime_env)

    display = reserve_display(state_path, session_id, profile_dir)
    cdp_port = cdp_port_for_display(display)
    live_view = live_view_for_display(display)
    xvfb_proc: PopenLike | None = None
    vnc_proc: PopenLike | None = None
    chrome_proc: PopenLike | None = None

    def _rollback() -> None:
        if chrome_proc is not None:
            try:
                chrome_proc.terminate()
            except OSError:
                pass
        if vnc_proc is not None:
            try:
                vnc_proc.terminate()
            except OSError:
                pass
        if xvfb_proc is not None:
            try:
                xvfb_proc.terminate()
            except OSError:
                pass
        release_display(state_path, display)
        if profile_dir.is_dir():
            shutil.rmtree(profile_dir, ignore_errors=True)

    try:
        xvfb_args = [
            xvfb_path or XVFB_BIN,
            f":{display}",
            "-screen",
            "0",
            "1920x1080x24",
            "-ac",
            "-nolisten",
            "tcp",
        ]
        xvfb_proc = popen_factory(
            xvfb_args,
            env={**runtime_env, "DISPLAY": f":{display}"},
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

        wait_for_x11_socket(display, socket_exists=socket_exists)

        vnc_port = 5900 + display
        vnc_args = [
            x11vnc_path or X11VNC_BIN,
            "-display",
            f":{display}",
            "-localhost",
            "-rfbport",
            str(vnc_port),
            "-nopw",
            "-shared",
            "-forever",
            "-noxdamage",
        ]
        vnc_proc = popen_factory(
            vnc_args,
            env={**runtime_env, "DISPLAY": f":{display}"},
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

        chrome_args = [
            chrome_bin,
            f"--user-data-dir={profile_dir}",
            "--new-window",
            "about:blank",
            f"--remote-debugging-port={cdp_port}",
            "--remote-debugging-address=127.0.0.1",
            "--no-first-run",
            "--no-default-browser-check",
        ]
        chrome_proc = popen_factory(
            chrome_args,
            env={**runtime_env, "DISPLAY": f":{display}"},
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

        time.sleep(CHROME_FAIL_GRACE_S)
        exit_code = chrome_proc.poll()
        if exit_code is not None:
            raise SessionError(
                f"Chrome exited immediately with code {exit_code}",
                code="chrome_failed",
            )

    except SessionError:
        _rollback()
        raise
    except Exception as exc:
        _rollback()
        raise SessionError(str(exc)) from exc

    return SessionReceipt(
        session_id=session_id,
        display=display,
        profile_dir=profile_dir,
        cdp_port=cdp_port,
        live_view=live_view,
    )


def main_cli(argv: Sequence[str] | None = None) -> int:
    """Entry point for ``scripts/box-isolated-chrome``."""
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Box isolated Chrome sessions")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("start", help="Start a new isolated Chrome session")
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.command != "start":
        return 2

    try:
        receipt = start_session()
    except SessionError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(json.dumps(receipt.to_json_dict(), indent=2))
    return 0
