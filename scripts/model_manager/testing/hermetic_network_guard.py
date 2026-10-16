"""Fail-closed network guard for manage controller offline tests (a:39456).

The autouse fixture is directory-scoped (see ``ui/conftest.py``). This module
does not register an autouse plugin fixture: a plugin fixture would apply to
every test in the pytest session, including trees this guard was not written for.
"""

from __future__ import annotations

import asyncio
import os
import shlex
import socket
import subprocess
from collections.abc import Callable
from typing import Any

import pytest

ALLOW_LIVE_NETWORK_MARKER = "allow_live_network"

_GUARD_MSG = (
    "hermetic network guard blocked live I/O "
    "(socket connect or docker subprocess); opt out with @pytest.mark.allow_live_network"
)

_DOCKER_ARGV0 = frozenset({"docker", "docker-compose"})

_GUARDED_PATH_MARKERS = (
    f"{os.sep}scripts{os.sep}model_manager{os.sep}ui{os.sep}controller{os.sep}",
    f"{os.sep}scripts{os.sep}model_manager{os.sep}ui{os.sep}test_api_dispatch_charter_harvest.py",
)


class HermeticNetworkGuardError(OSError):
    """Raised when a guarded test reaches for a live socket or docker.

    Subclasses ``OSError`` so fail-closed ``except OSError`` paths treat the
    block like a refused connect. The cortex events publisher is a
    process-lifetime daemon (``event_publisher.py``); an uncaught
    ``RuntimeError`` there becomes a session-wide pytest thread warning.
    """


def path_is_guarded(node_path: str) -> bool:
    """True for the controller tree and the charter-harvest module only."""
    return any(marker in node_path for marker in _GUARDED_PATH_MARKERS)


def _token_is_docker(token: str) -> bool:
    return os.path.basename(token) in _DOCKER_ARGV0


def _command_is_docker(args: Any) -> bool:
    if isinstance(args, (list, tuple)):
        if not args or not isinstance(args[0], str):
            return False
        return _token_is_docker(args[0])
    if isinstance(args, str):
        try:
            parts = shlex.split(args)
        except ValueError:
            parts = args.split()
        if not parts or not isinstance(parts[0], str):
            return False
        return _token_is_docker(parts[0])
    return False


def _block_address(address: object) -> None:
    if isinstance(address, (str, bytes, bytearray)):
        raise HermeticNetworkGuardError(_GUARD_MSG)
    if isinstance(address, tuple) and len(address) >= 2:
        raise HermeticNetworkGuardError(_GUARD_MSG)


def install_hermetic_network_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch socket connect paths and docker subprocess entry points."""
    real_connect: Callable[..., object] = socket.socket.connect

    def _guarded_connect(self: socket.socket, address: object) -> object:
        _block_address(address)
        return real_connect(self, address)

    monkeypatch.setattr(socket.socket, "connect", _guarded_connect)

    real_create_connection = socket.create_connection

    def _guarded_create_connection(
        address: object, *args: object, **kwargs: object
    ) -> socket.socket:
        _block_address(address)
        return real_create_connection(address, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(socket, "create_connection", _guarded_create_connection)

    real_open_unix = asyncio.open_unix_connection

    async def _guarded_open_unix(
        path: str | bytes | None = None, *args: object, **kwargs: object
    ) -> tuple[Any, Any]:
        if path is not None or args:
            _block_address(path if path is not None else args[0])
        return await real_open_unix(path, *args, **kwargs)  # type: ignore[arg-type,misc]

    monkeypatch.setattr(asyncio, "open_unix_connection", _guarded_open_unix)

    def _guard_subprocess(args: Any) -> None:
        if _command_is_docker(args):
            raise HermeticNetworkGuardError(_GUARD_MSG)

    def _wrap_subprocess_fn(real_fn: Callable[..., Any]) -> Callable[..., Any]:
        def _wrapped(*args: object, **kwargs: object) -> Any:
            cmd = kwargs.get("args") if "args" in kwargs else (args[0] if args else None)
            _guard_subprocess(cmd)
            return real_fn(*args, **kwargs)

        return _wrapped

    for name in ("run", "call", "check_call", "check_output"):
        monkeypatch.setattr(
            subprocess, name, _wrap_subprocess_fn(getattr(subprocess, name))
        )

    real_popen = subprocess.Popen

    def _guarded_popen(*args: object, **kwargs: object) -> subprocess.Popen[Any]:
        cmd = kwargs.get("args") if "args" in kwargs else (args[0] if args else None)
        _guard_subprocess(cmd)
        return real_popen(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(subprocess, "Popen", _guarded_popen)

    real_exec = asyncio.create_subprocess_exec

    async def _guarded_exec(
        *args: object, **kwargs: object
    ) -> asyncio.subprocess.Process:
        _guard_subprocess(args)
        return await real_exec(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _guarded_exec)

    real_shell = asyncio.create_subprocess_shell

    async def _guarded_shell(
        cmd: str, *args: object, **kwargs: object
    ) -> asyncio.subprocess.Process:
        _guard_subprocess(cmd)
        return await real_shell(cmd, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(asyncio, "create_subprocess_shell", _guarded_shell)


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        f"{ALLOW_LIVE_NETWORK_MARKER}: allow socket connect and docker subprocess "
        "(local listener or stub binary only; never the live manage port or docker CLI)",
    )
