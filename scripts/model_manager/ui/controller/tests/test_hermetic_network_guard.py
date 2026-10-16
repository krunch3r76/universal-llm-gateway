"""Guard-trip evidence for the manage controller hermetic network guard (AC-2)."""

from __future__ import annotations

import socket
import subprocess

import pytest

from scripts.model_manager.testing.hermetic_network_guard import (
    HermeticNetworkGuardError,
)

pytestmark = pytest.mark.offline


def test_hermetic_guard_blocks_inet_socket_connect() -> None:
    with pytest.raises(HermeticNetworkGuardError, match="hermetic network guard"):
        socket.create_connection(("127.0.0.1", 8091), timeout=0.05)


@pytest.mark.allow_live_network
def test_hermetic_guard_allows_inet_socket_when_opted_out() -> None:
    try:
        socket.create_connection(("127.0.0.1", 8091), timeout=0.05)
    except OSError as exc:
        assert "hermetic network guard" not in str(exc).lower()


def test_hermetic_guard_blocks_docker_subprocess() -> None:
    with pytest.raises(HermeticNetworkGuardError, match="hermetic network guard"):
        subprocess.run(  # noqa: S603
            ["docker", "inspect", "mcp-server"],
            capture_output=True,
            check=False,
        )


@pytest.mark.allow_live_network
def test_hermetic_guard_allows_docker_subprocess_when_opted_out() -> None:
    try:
        subprocess.run(  # noqa: S603
            ["docker", "inspect", "mcp-server"],
            capture_output=True,
            check=False,
        )
    except HermeticNetworkGuardError:
        pytest.fail("guard tripped on opt-out test")
    except (FileNotFoundError, OSError):
        pass
