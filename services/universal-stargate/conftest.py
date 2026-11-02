"""Stargate-wide pytest hooks shared across ``services/universal-stargate`` suites.

Autouse fixtures here isolate offline tests from the live git-integration-worker
work registry unless a test opts in with ``@pytest.mark.live_giw``.
"""

from __future__ import annotations

import socket
from collections.abc import Iterator

import pytest


def _bind_ephemeral_loopback_port() -> int:
    """Reserve a free TCP port on 127.0.0.1 and release it (no listener remains)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(scope="session")
def giw_dead_port() -> int:
    """Session-scoped loopback port with no listener for GIW isolation."""
    return _bind_ephemeral_loopback_port()


def _dead_giw_url(port: int) -> str:
    return f"http://127.0.0.1:{port}"


@pytest.fixture(autouse=True)
def _isolate_giw_registry_relay(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
    giw_dead_port: int,
) -> Iterator[None]:
    """Point GIW env at a dead port unless ``live_giw``."""
    if request.node.get_closest_marker("live_giw") is not None:
        yield
        return

    dead_url = _dead_giw_url(giw_dead_port)
    monkeypatch.setenv("GIT_INTEGRATION_WORKER_HOST", "127.0.0.1")
    monkeypatch.setenv("GIT_INTEGRATION_WORKER_PORT", str(giw_dead_port))
    monkeypatch.setenv("GIT_INTEGRATION_WORKER_URL", dead_url)
    yield
