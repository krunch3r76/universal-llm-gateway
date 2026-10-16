"""Fail-closed network guard for manage controller offline tests (a:39456).

The autouse fixture is directory-scoped (see ``ui/conftest.py``). This module
does not register an autouse plugin fixture: a plugin fixture would apply to
every test in the pytest session, including trees this guard was not written for.
"""

from __future__ import annotations

import asyncio
import os
import re
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


class HermeticNetworkGuardError(Exception):
    """Raised when a guarded test reaches for a live socket or docker.

    Not an ``OSError``. ``except OSError`` paths (the cortex events publisher
    retry in ``event_publisher.py``) must not treat a trip as a down socket
    and connect again after the fixture undoes the patch.
    """


def path_is_guarded(node_path: str) -> bool:
    """True for the controller tree and the charter-harvest module only.

    Resolves symlinks so a checkout reached through a linked
    ``scripts/model_manager`` still matches the markers.
    """
    try:
        resolved = os.path.realpath(node_path)
    except OSError:
        resolved = node_path
    return any(marker in resolved for marker in _GUARDED_PATH_MARKERS)


def _token_is_docker(token: str) -> bool:
    return os.path.basename(token) in _DOCKER_ARGV0


_SUBSTITUTION = re.compile(
    r"(?:\$\(|`)[ \t]*docker(?:-compose)?\b"
)


def _piece_invokes_docker(piece: str) -> bool:
    """True for a docker argv0, including one glued to ``;`` or ``|``."""
    for chunk in re.split(r"[;&|]+", piece):
        token = chunk.strip()
        if token and _token_is_docker(token):
            return True
    return False


def _shell_invokes_docker(cmd: str) -> bool:
    """True when a shell string would run docker or docker-compose.

    Scans every shlex token, not only argv0, and splits tokens glued to
    ``;`` (``echo x;docker ps``). A token that is itself a shell snippet
    (``bash -c`` argument ``docker ps``, ``$(docker ps)``, backticks) is
    scanned the same way. An unmatched quote raises ``ValueError``; that
    string is not a docker launch, and the whitespace split does not broaden
    it into one.
    """
    try:
        parts = shlex.split(cmd)
    except ValueError:
        parts = cmd.split()
        return any(_piece_invokes_docker(part) for part in parts)
    for part in parts:
        if _SUBSTITUTION.search(part):
            return True
        if _piece_invokes_docker(part):
            return True
        if any(ch.isspace() for ch in part) and _shell_invokes_docker(part):
            return True
    return False


def _command_is_docker(args: Any) -> bool:
    if isinstance(args, (list, tuple)):
        return any(
            isinstance(item, str)
            and (_token_is_docker(item) or _shell_invokes_docker(item))
            for item in args
        )
    if isinstance(args, str):
        return _shell_invokes_docker(args)
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
