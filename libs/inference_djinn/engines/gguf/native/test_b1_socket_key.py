"""B1: Unix socket paths keyed by backend (friction:39573)."""

from __future__ import annotations

import asyncio
import socket
import threading
from pathlib import Path

import pytest

from inference_djinn.engines.gguf.native.config import ServerConfig
from inference_djinn.engines.gguf.native.engine import gguf_unix_socket_key
from inference_djinn.engines.gguf.native.server import (
    LlamaServerManager,
    ServerStatus,
    prepare_unix_socket_for_bind,
)

GGUF = "/models/shared/qwen3-embedding-8b-Q4_K_M.gguf"


def test_b1_socket_key_differs_gpu_vs_cpu_same_gguf() -> None:
    gpu_key = gguf_unix_socket_key(GGUF, n_gpu_layers=-1)
    cpu_key = gguf_unix_socket_key(GGUF, n_gpu_layers=0)
    assert gpu_key != cpu_key


def test_b1_socket_key_stable_for_same_variant() -> None:
    a = gguf_unix_socket_key(GGUF, n_gpu_layers=-1)
    b = gguf_unix_socket_key(GGUF, n_gpu_layers=99)
    c = gguf_unix_socket_key(GGUF, n_gpu_layers=-1)
    assert a == c
    assert a == b


class _UnixAcceptStub:
    """Minimal AF_UNIX listener for connect(2) probe tests."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._path.exists():
            self._path.unlink()
        self._sock.bind(str(self._path))
        self._sock.listen(1)
        self._sock.settimeout(0.2)

        def _loop() -> None:
            while not self._stop.is_set():
                try:
                    conn, _ = self._sock.accept()
                    conn.close()
                except TimeoutError:
                    continue
                except OSError:
                    break

        self._thread = threading.Thread(target=_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._sock.close()
        if self._thread:
            self._thread.join(timeout=2.0)


def test_b1_start_does_not_unlink_live_socket(tmp_path: Path) -> None:
    sock_path = tmp_path / "live.sock"
    stub = _UnixAcceptStub(sock_path)
    stub.start()
    try:
        with pytest.raises(RuntimeError, match="already in use"):
            prepare_unix_socket_for_bind(str(sock_path))
        assert sock_path.exists()
    finally:
        stub.stop()


def test_b1_stop_unlinks_only_own_socket(tmp_path: Path) -> None:
    own = tmp_path / "own.sock"
    other = tmp_path / "other.sock"
    own.touch()
    other.touch()

    config = ServerConfig(model_path=GGUF, socket_path=str(own))
    manager = LlamaServerManager(config)
    manager.status = ServerStatus.RUNNING

    async def _stop() -> None:
        await manager.stop()

    asyncio.run(_stop())

    assert not own.exists()
    assert other.exists()


def test_b1_two_backends_two_live_sockets_one_stop(tmp_path: Path) -> None:
    """Same gguf, gpu vs cpu keys ⇒ two sockets; stopping one leaves the other live."""
    gpu_path = tmp_path / f"{gguf_unix_socket_key(GGUF, -1)}.sock"
    cpu_path = tmp_path / f"{gguf_unix_socket_key(GGUF, 0)}.sock"
    gpu_stub = _UnixAcceptStub(gpu_path)
    cpu_stub = _UnixAcceptStub(cpu_path)
    gpu_stub.start()
    cpu_stub.start()

    def _connect_ok(path: Path) -> None:
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(1.0)
        client.connect(str(path))
        client.close()

    try:
        _connect_ok(gpu_path)
        _connect_ok(cpu_path)

        gpu_manager = LlamaServerManager(
            ServerConfig(model_path=GGUF, socket_path=str(gpu_path), n_gpu_layers=-1)
        )
        gpu_manager.status = ServerStatus.RUNNING
        asyncio.run(gpu_manager.stop())

        assert not gpu_path.exists()
        _connect_ok(cpu_path)
    finally:
        cpu_stub.stop()
