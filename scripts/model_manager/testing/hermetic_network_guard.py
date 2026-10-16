"""Fail-closed network guard for manage controller offline tests (a:39456)."""

from __future__ import annotations

import asyncio
import os
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


class HermeticNetworkGuardError(RuntimeError):
    """Raised when a test reaches for a live socket or docker without opting out."""


def _argv0_is_docker(args: Any) -> bool:
    if not args:
        return False
    first = args[0] if isinstance(args, (list, tuple)) else args
    if not isinstance(first, str):
        return False
    return os.path.basename(first) == "docker"


def _block_address(address: object) -> None:
    if isinstance(address, str):
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
        path: str | None = None, *args: object, **kwargs: object
    ) -> tuple[Any, Any]:
        if path is not None or args:
            raise HermeticNetworkGuardError(_GUARD_MSG)
        return await real_open_unix(path, *args, **kwargs)  # type: ignore[arg-type,misc]

    monkeypatch.setattr(asyncio, "open_unix_connection", _guarded_open_unix)

    def _guard_subprocess(args: Any, *_a: object, **_k: object) -> None:
        if _argv0_is_docker(args):
            raise HermeticNetworkGuardError(_GUARD_MSG)

    def _wrap_subprocess_fn(real_fn: Callable[..., Any]) -> Callable[..., Any]:
        def _wrapped(*args: object, **kwargs: object) -> Any:
            cmd = kwargs.get("args") if "args" in kwargs else (args[0] if args else None)
            _guard_subprocess(cmd)
            return real_fn(*args, **kwargs)

        return _wrapped

    for name in ("run", "call", "check_call", "check_output"):
        monkeypatch.setattr(subprocess, name, _wrap_subprocess_fn(getattr(subprocess, name)))

    real_popen = subprocess.Popen

    def _guarded_popen(*args: object, **kwargs: object) -> subprocess.Popen[Any]:
        cmd = kwargs.get("args") if "args" in kwargs else (args[0] if args else None)
        _guard_subprocess(cmd)
        return real_popen(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(subprocess, "Popen", _guarded_popen)

    real_exec = asyncio.create_subprocess_exec

    async def _guarded_exec(*args: object, **kwargs: object) -> asyncio.subprocess.Process:
        _guard_subprocess(args)
        return await real_exec(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _guarded_exec)

    real_shell = asyncio.create_subprocess_shell

    async def _guarded_shell(
        cmd: str, *args: object, **kwargs: object
    ) -> asyncio.subprocess.Process:
        if cmd.strip().startswith("docker") or " docker " in f" {cmd} ":
            raise HermeticNetworkGuardError(_GUARD_MSG)
        return await real_shell(cmd, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(asyncio, "create_subprocess_shell", _guarded_shell)


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        f"{ALLOW_LIVE_NETWORK_MARKER}: allow socket connect and docker subprocess "
        "(local test server only)",
    )


@pytest.fixture(autouse=True)
def _hermetic_network_guard_autouse(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    if request.node.get_closest_marker(ALLOW_LIVE_NETWORK_MARKER):
        return
    install_hermetic_network_guard(monkeypatch)
