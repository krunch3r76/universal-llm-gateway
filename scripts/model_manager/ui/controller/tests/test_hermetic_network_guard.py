"""Guard-trip evidence for the manage controller hermetic network guard (AC-2)."""

from __future__ import annotations

import asyncio
import os
import socket
import stat
import subprocess
import threading

import pytest

from scripts.model_manager.testing.hermetic_network_guard import (
    HermeticNetworkGuardError,
)

pytestmark = pytest.mark.offline

_EVENTS_SOCK = b"/tmp/universal-protocol/events.sock"


def test_hermetic_guard_blocks_inet_socket_connect() -> None:
    with pytest.raises(HermeticNetworkGuardError, match="hermetic network guard"):
        socket.create_connection(("127.0.0.1", 8091), timeout=0.05)


@pytest.mark.allow_live_network
def test_hermetic_guard_allows_inet_socket_when_opted_out() -> None:
    """Opt-out connects to a listener this test binds. It does not dial :8091."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    _host, port = listener.getsockname()
    try:
        client = socket.create_connection(("127.0.0.1", port), timeout=1.0)
        client.close()
    finally:
        listener.close()


def test_hermetic_guard_blocks_docker_subprocess() -> None:
    with pytest.raises(HermeticNetworkGuardError, match="hermetic network guard"):
        subprocess.run(  # noqa: S603
            ["docker", "inspect", "mcp-server"],
            capture_output=True,
            check=False,
        )


@pytest.mark.allow_live_network
def test_hermetic_guard_allows_docker_subprocess_when_opted_out(
    tmp_path: object,
) -> None:
    """Opt-out executes a stub binary named docker, not the real docker CLI."""
    from pathlib import Path

    root = Path(str(tmp_path))
    stub = root / "docker"
    stub.write_text("#!/bin/sh\necho stub-docker\n")
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    env = os.environ.copy()
    env["PATH"] = str(root) + os.pathsep + env.get("PATH", "")
    result = subprocess.run(  # noqa: S603
        ["docker", "inspect", "mcp-server"],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert result.returncode == 0
    assert "stub-docker" in result.stdout


def test_hermetic_guard_blocks_unix_bytes_address() -> None:
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        with pytest.raises(HermeticNetworkGuardError, match="hermetic network guard"):
            sock.connect(_EVENTS_SOCK)
    finally:
        sock.close()


def test_hermetic_guard_blocks_docker_compose_argv0() -> None:
    with pytest.raises(HermeticNetworkGuardError, match="hermetic network guard"):
        subprocess.run(  # noqa: S603
            ["docker-compose", "ps"],
            capture_output=True,
            check=False,
        )


def test_hermetic_guard_blocks_docker_absolute_path_shell() -> None:
    async def _launch() -> None:
        await asyncio.create_subprocess_shell("/usr/bin/docker inspect mcp-server")

    with pytest.raises(HermeticNetworkGuardError, match="hermetic network guard"):
        asyncio.run(_launch())


def test_guard_trip_is_not_an_oserror() -> None:
    """``except OSError`` must not see a guard trip (publisher retry)."""
    seen: list[tuple[str, type[BaseException]]] = []

    def _connect() -> None:
        try:
            socket.create_connection(("127.0.0.1", 1), timeout=0.05)
        except OSError as exc:
            seen.append(("oserror", type(exc)))
        except Exception as exc:
            seen.append(("other", type(exc)))

    worker = threading.Thread(target=_connect, daemon=True)
    worker.start()
    worker.join(timeout=2)
    assert seen == [("other", HermeticNetworkGuardError)]
    assert not issubclass(HermeticNetworkGuardError, OSError)


def _assert_shell_blocked(cmd: str) -> None:
    async def _launch() -> None:
        await asyncio.create_subprocess_shell(cmd)

    with pytest.raises(HermeticNetworkGuardError, match="hermetic network guard"):
        asyncio.run(_launch())


def test_shell_sudo_docker_blocked() -> None:
    _assert_shell_blocked("sudo docker ps")


def test_shell_env_docker_blocked() -> None:
    _assert_shell_blocked("env docker ps")


def test_shell_semicolon_docker_blocked() -> None:
    _assert_shell_blocked("echo x; docker ps")


def test_shell_exec_docker_blocked() -> None:
    _assert_shell_blocked("exec docker ps")


def test_shell_command_docker_blocked() -> None:
    _assert_shell_blocked("command docker ps")


def test_path_is_guarded_resolves_model_manager_symlink(tmp_path: object) -> None:
    from pathlib import Path

    from scripts.model_manager.testing.hermetic_network_guard import path_is_guarded

    root = Path(str(tmp_path))
    link = root / "model_manager"
    link.symlink_to(Path("scripts/model_manager").resolve())
    node = link / "ui" / "controller" / "tests" / "test_hermetic_network_guard.py"
    raw = str(node)
    marker = f"{os.sep}scripts{os.sep}model_manager{os.sep}ui{os.sep}controller{os.sep}"
    assert marker not in raw
    assert path_is_guarded(raw) is True
