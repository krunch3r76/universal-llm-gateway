"""Tests for manage.sock _is_socket_alive guard (fail-closed on ambiguous probe)."""

from __future__ import annotations

import socket
from pathlib import Path

import pytest

from scripts.model_manager.ui.api_server import (
    SocketProbeAmbiguousError,
    _is_socket_alive,
)


@pytest.mark.offline
def test_healthy_listener_empty_backlog_reported_alive(tmp_path: Path) -> None:
    path = tmp_path / "healthy.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(path))
    listener.listen(100)
    try:
        assert _is_socket_alive(path) is True
    finally:
        listener.close()


@pytest.mark.offline
def test_nonexistent_path_reported_dead(tmp_path: Path) -> None:
    assert _is_socket_alive(tmp_path / "nope.sock") is False


@pytest.mark.offline
def test_saturated_backlog_treated_as_possibly_alive(tmp_path: Path) -> None:
    """Live listener with full accept backlog must not be classified dead."""
    path = tmp_path / "wedged.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(path))
    listener.listen(1)
    holders: list[socket.socket] = []
    try:
        for _ in range(12):
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client.settimeout(0.2)
            try:
                client.connect(str(path))
                holders.append(client)
            except OSError:
                client.close()
                break
        with pytest.raises(SocketProbeAmbiguousError):
            _is_socket_alive(path)
    finally:
        for client in holders:
            client.close()
        listener.close()
