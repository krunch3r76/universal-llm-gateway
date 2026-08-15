"""Death-branch helpers for the GIW wedge watcher — manage restart + budgets."""

from __future__ import annotations

import json
import os
import socket
import time
from collections import deque
from typing import Any

_MANAGE_SOCKET = os.environ.get("MANAGE_SOCKET", "/tmp/universal-protocol/manage.sock")
_GIW_SERVICE = "git_integration_worker"
_RESTART_WINDOW_S = 1800.0
_RESTART_CAP = 3


class ActionBudget:
    """Sliding-window cap — at most *cap* actions per *window_s*."""

    def __init__(
        self, *, cap: int = _RESTART_CAP, window_s: float = _RESTART_WINDOW_S
    ) -> None:
        self.cap = cap
        self.window_s = window_s
        self._stamps: deque[float] = deque()

    def remaining(self, now: float | None = None) -> int:
        self._prune(now if now is not None else time.monotonic())
        return max(0, self.cap - len(self._stamps))

    def allow(self, now: float | None = None) -> bool:
        return self.remaining(now) > 0

    def record(self, now: float | None = None) -> None:
        self._stamps.append(now if now is not None else time.monotonic())

    def _prune(self, now: float) -> None:
        cutoff = now - self.window_s
        while self._stamps and self._stamps[0] < cutoff:
            self._stamps.popleft()


def manage_rpc(
    method: str, params: dict[str, Any] | None = None, *, timeout: float = 15.0
) -> dict[str, Any]:
    """One-shot JSON-RPC to manage.sock. Returns result or ``{"error": ...}``."""
    body = {
        "jsonrpc": "2.0",
        "method": method,
        "params": params or {},
        "id": 1,
    }
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect(_MANAGE_SOCKET)
            sock.sendall(json.dumps(body).encode() + b"\n")
            data = b""
            while True:
                chunk = sock.recv(65_536)
                if not chunk:
                    break
                data += chunk
                if b"\n" in data:
                    break
    except OSError as exc:
        return {"error": f"manage_sock:{exc}"}
    try:
        raw = json.loads(data.strip() or b"{}")
    except json.JSONDecodeError:
        return {"error": f"manage_bad_json:{data[:200]!r}"}
    if "error" in raw:
        err = raw["error"]
        message = err.get("message", str(err)) if isinstance(err, dict) else str(err)
        return {"error": message}
    result = raw.get("result", raw)
    return result if isinstance(result, dict) else {"result": result}


def restart_window_open() -> bool:
    """True when manage reports any open restart window (operator maintenance)."""
    status = manage_rpc("busy_status")
    if "error" in status:
        return False
    windows = status.get("restart_windows") or {}
    open_windows = windows.get("open") or []
    return bool(open_windows)


def start_giw() -> dict[str, Any]:
    """Ask manage to start git_integration_worker."""
    return manage_rpc("start", {"service": _GIW_SERVICE})
