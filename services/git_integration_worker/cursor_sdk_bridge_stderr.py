"""Drain and retain the cursor-sdk bridge subprocess stderr for post-mortem use.

The vendored ``cursor_sdk`` launcher spawns the Node bridge with
``stderr=subprocess.PIPE`` and reads that pipe only while waiting for the
``cursor-sdk-bridge ready`` discovery line (``cursor_sdk/_bridge.py``
``_read_discovery``). Nothing reads it afterwards, and ``Bridge.close()`` does
not drain it either. A bridge that dies mid-dispatch therefore takes its exit
code and its last words with it, leaving GIW to observe only the follow-up
``ConnectError: [Errno 111] Connection refused`` (assertion 31706).

Draining the pipe serves two ends: the exit reason survives into the failure
envelope, and the Node process stops accumulating its own stderr in memory once
the kernel pipe buffer fills.
"""

from __future__ import annotations

import os
import signal as signal_module
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from universal_logging import get_logger

from services.git_integration_worker.cursor_sdk_bridge_events import (
    emit_sdk_bridge_exited,
)
from services.git_integration_worker.cursor_sdk_events import terminal_emitted

logger = get_logger(__name__)

_STDERR_DIR = Path(
    os.getenv(
        "GIT_WORKER_BRIDGE_STDERR_DIR",
        "/tmp/logs/git-integration-worker/bridge-stderr",
    )
)
# Head bytes written verbatim; beyond this only the rolling tail is retained so
# a chatty bridge cannot fill /tmp.
_MAX_HEAD_BYTES = 1_048_576
_TAIL_LINES = 40
_JOIN_TIMEOUT_S = 2.0


@dataclass
class BridgeStderrTap:
    """Live drain of one bridge subprocess's stderr pipe."""

    dispatch_id: str
    thread_id: str
    log_path: Path
    process: subprocess.Popen[str]
    started_at: float
    thread: threading.Thread | None = None
    expected_exit: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _tail: deque[str] = field(default_factory=lambda: deque(maxlen=_TAIL_LINES))
    _bytes: int = 0

    def tail(self) -> list[str]:
        """Return the retained trailing stderr lines."""
        with self._lock:
            return list(self._tail)

    def byte_count(self) -> int:
        """Return total stderr bytes seen, including bytes past the file cap."""
        with self._lock:
            return self._bytes


def _resolve_bridge_process(client: Any) -> subprocess.Popen[str] | None:
    """Return the Popen behind an SDK-owned bridge, or None when unavailable.

    Reaches a private ``cursor_sdk`` attribute because the package exposes no
    public handle on the subprocess it launched; GIW already depends on that
    module's internals to overlay the bridge subprocess environment.
    """
    bridge = getattr(client, "_owned_bridge", None)
    process = getattr(bridge, "process", None)
    # isinstance, not truthiness: test doubles expose a ``process`` attribute
    # whose pipe reads and returncode comparisons are not real.
    if not isinstance(process, subprocess.Popen) or process.stderr is None:
        return None
    return process


def _decode_exit(returncode: int | None) -> tuple[int | None, str | None]:
    """Split a Popen returncode into (exit_code, signal_name)."""
    if returncode is None:
        return None, None
    if returncode < 0:
        try:
            return None, signal_module.Signals(-returncode).name
        except ValueError:
            return None, f"SIG{-returncode}"
    return returncode, None


def _drain(tap: BridgeStderrTap) -> None:
    """Read the bridge stderr pipe to EOF, mirroring it to disk and a tail ring."""
    stream = tap.process.stderr
    if stream is None:
        return
    handle = None
    written = 0
    try:
        handle = tap.log_path.open("a", encoding="utf-8", errors="replace")
        while True:
            line = stream.readline()
            if not line:
                break
            encoded = len(line.encode("utf-8", errors="replace"))
            with tap._lock:
                tap._bytes += encoded
                tap._tail.append(line.rstrip("\n"))
            if written < _MAX_HEAD_BYTES:
                handle.write(line)
                handle.flush()
                written += encoded
                if written >= _MAX_HEAD_BYTES:
                    handle.write(
                        f"--- head cap {_MAX_HEAD_BYTES} bytes reached; "
                        "retaining tail only ---\n"
                    )
                    handle.flush()
    except Exception as exc:  # noqa: BLE001 — forensics must never break dispatch
        logger.warning(
            "cursor sdk bridge stderr drain failed: dispatch_id=%s err=%s",
            tap.dispatch_id,
            exc,
        )
    finally:
        if handle is not None:
            try:
                if tap.byte_count() > written:
                    handle.write("--- retained tail ---\n")
                    for line in tap.tail():
                        handle.write(f"{line}\n")
                handle.close()
            except Exception:  # noqa: BLE001
                pass
    try:
        _on_eof(tap)
    except Exception as exc:  # noqa: BLE001 — a drain thread must never raise
        logger.warning(
            "cursor sdk bridge exit signal failed: dispatch_id=%s err=%s",
            tap.dispatch_id,
            exc,
        )


def _on_eof(tap: BridgeStderrTap) -> None:
    """Emit the bridge-exit signal when the pipe closed without a planned teardown."""
    if tap.expected_exit:
        return
    try:
        tap.process.wait(timeout=_JOIN_TIMEOUT_S)
    except Exception:  # noqa: BLE001 — exit code is best-effort
        pass
    if terminal_emitted(tap.dispatch_id):
        return
    exit_code, signal_name = _decode_exit(tap.process.returncode)
    emit_sdk_bridge_exited(
        dispatch_id=tap.dispatch_id,
        thread_id=tap.thread_id,
        exit_code=exit_code,
        signal_name=signal_name,
        elapsed_s=round(time.monotonic() - tap.started_at, 1),
        stderr_bytes=tap.byte_count(),
        stderr_tail=tap.tail(),
        log_path=str(tap.log_path),
    )


def start_bridge_stderr_drain(
    *,
    dispatch_id: str,
    thread_id: str,
    client: Any,
) -> BridgeStderrTap | None:
    """Begin draining the bridge stderr pipe for *dispatch_id*.

    Returns None when the client did not launch its own bridge (resumed or
    externally supplied endpoints), which is not an error.
    """
    process = _resolve_bridge_process(client)
    if process is None:
        return None
    try:
        _STDERR_DIR.mkdir(parents=True, exist_ok=True)
    except Exception as exc:  # noqa: BLE001 — never block a dispatch on logging
        logger.warning(
            "cursor sdk bridge stderr dir unavailable: dispatch_id=%s err=%s",
            dispatch_id,
            exc,
        )
        return None
    tap = BridgeStderrTap(
        dispatch_id=dispatch_id,
        thread_id=thread_id,
        log_path=_STDERR_DIR / f"{dispatch_id}.log",
        process=process,
        started_at=time.monotonic(),
    )
    thread = threading.Thread(
        target=_drain,
        args=(tap,),
        name=f"bridge-stderr-{dispatch_id}",
        daemon=True,
    )
    tap.thread = thread
    thread.start()
    return tap


def bridge_exit_snapshot(tap: BridgeStderrTap | None) -> dict[str, Any]:
    """Return forensics about the bridge subprocess state, or {} when untapped."""
    if tap is None:
        return {}
    exit_code, signal_name = _decode_exit(tap.process.poll())
    snapshot: dict[str, Any] = {
        "bridge_exit_code": exit_code,
        "bridge_signal": signal_name,
        "bridge_alive": tap.process.poll() is None,
        "bridge_stderr_bytes": tap.byte_count(),
        "bridge_stderr_log": str(tap.log_path),
    }
    tail = tap.tail()
    if tail:
        snapshot["bridge_stderr_tail"] = tail
    return snapshot


def stop_bridge_stderr_drain(tap: BridgeStderrTap | None) -> None:
    """Mark the tap as deliberately torn down so its exit raises no signal.

    Call before closing the client. When the bridge is still alive the drain
    thread is parked on ``readline`` and only wakes on the EOF that the close
    produces, so joining here would buy nothing but the timeout.
    """
    if tap is None:
        return
    tap.expected_exit = True
    thread = tap.thread
    if thread is None or not thread.is_alive():
        return
    if tap.process.poll() is None:
        return
    thread.join(timeout=_JOIN_TIMEOUT_S)
