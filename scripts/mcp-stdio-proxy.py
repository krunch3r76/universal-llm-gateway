#!/usr/bin/env python3
"""Optional stdio → HTTP fallback proxy for the vortex MCP server.

Steady-state Cursor integration uses direct HTTPS MCP. This proxy remains
available for fallback/debug transport scenarios and should stay transport-
focused (timeouts, heartbeat forwarding, relay telemetry), not tool policy.

It translates MCP JSON-RPC messages from stdin to HTTP POST requests
against the real MCP server, and writes responses back to stdout.

Architecture — fully concurrent stdio:
  - A dedicated reader thread drains stdin continuously so Cursor's
    pipe writes never block (preventing extension-host freezes).
  - Each inbound message is dispatched to its own handler thread for
    HTTP processing.  Multiple tool calls can be in flight concurrently.
  - stdout is protected by a lock so responses never interleave.

The server returns chunked SSE with keep-alive connections, so we read
incrementally and return as soon as a complete event arrives.

Heartbeat SSE events are forwarded as JSON-RPC progress notifications to
keep the stdio pipe visibly active during long-running tool calls.  A
watchdog thread enforces a hard timeout so the proxy never blocks the
IDE indefinitely.

Structured events are emitted to the event service UDS at
/tmp/universal-protocol/events.sock (same NDJSON wire format as all
gateway services).  This makes proxy lifecycle, request latency, and
timeout events queryable via observability.

Connects to MCP_URL with proper TLS hostname verification. The default
uses the public hostname (mcp.k-1.me) with /etc/hosts resolving it to
127.0.0.1 for local connections.

Example fallback config in `.cursor/mcp.json`:
    "vortex-fallback": {
        "command": "python3",
        "args": ["/mnt/torus/projects/universal-llm-gateway/scripts/mcp-stdio-proxy.py"]
    }
"""

from __future__ import annotations

import datetime
import json
import os
import queue
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Literal

# Connect via hostname so TLS hostname verification works correctly.
# /etc/hosts resolves mcp.k-1.me → 127.0.0.1 for local connections.
MCP_URL = os.environ.get("MCP_URL", "https://mcp.k-1.me/mcp")

# Primary timeout: watchdog kills the request after this many seconds.
# Safety-net: urllib socket timeout is set slightly higher so the watchdog
# fires first under normal conditions.
_WATCHDOG_TIMEOUT = int(os.environ.get("MCP_PROXY_TIMEOUT", "60"))
_SOCKET_TIMEOUT = _WATCHDOG_TIMEOUT + 30  # safety net beyond watchdog

# How often to poll the result queue when no events arrive.
_POLL_INTERVAL = 2.0

# Maximum concurrent in-flight requests.
_MAX_INFLIGHT = int(os.environ.get("MCP_PROXY_MAX_INFLIGHT", "8"))

# Emit a warning event when a response exceeds this size (defense-in-depth;
# the server-side guard should prevent oversized payloads, but this catches
# any that slip through and helps diagnose stdio pipe stalls).
_LARGE_RESPONSE_BYTES = int(
    os.environ.get("MCP_PROXY_LARGE_RESPONSE_BYTES", str(32 * 1024))
)

# Event service UDS — bind-mounted from host into Docker at same path.
_EVENTS_SOCK = os.environ.get("EVENTS_SOCK", "/tmp/universal-protocol/events.sock")
_EVENTS_ENABLED = os.environ.get("MCP_PROXY_EVENTS", "true").lower() in {
    "true",
    "1",
    "yes",
}


# ── Event emitter (UDS, fire-and-forget) ─────────────────────────────


class _EventEmitter:
    """Lightweight UDS event publisher matching the gateway NDJSON wire format.

    Non-blocking: events are queued and sent from a daemon thread.
    If the socket is unavailable, events are silently dropped.
    """

    def __init__(self, sock_path: str) -> None:
        self._sock_path = sock_path
        self._q: queue.Queue[str] = queue.Queue(maxsize=200)
        self._pid = os.getpid()
        t = threading.Thread(target=self._run, daemon=True, name="proxy-events")
        t.start()

    def emit(self, signal: str, **payload: Any) -> None:
        """Queue a structured event for delivery."""
        now = datetime.datetime.now(datetime.UTC)
        event = {
            "signal": signal,
            "source": "mcp-stdio-proxy",
            "role": "observation",
            "scope": "global",
            "timestamp": now.isoformat(),
            "ts_unix_ms": int(now.timestamp() * 1000),
            "payload": {"pid": self._pid, **payload},
        }
        line = json.dumps(event, default=str) + "\n"
        try:
            self._q.put_nowait(line)
        except queue.Full:
            try:
                self._q.get_nowait()
            except queue.Empty:
                pass
            try:
                self._q.put_nowait(line)
            except queue.Full:
                pass

    def _run(self) -> None:
        sock: socket.socket | None = None
        while True:
            if sock is None:
                try:
                    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    sock.settimeout(2.0)
                    sock.connect(self._sock_path)
                except OSError:
                    if sock is not None:
                        try:
                            sock.close()
                        except OSError:
                            pass
                    sock = None
                    time.sleep(5.0)
                    continue
            try:
                line = self._q.get(timeout=1.0)
                sock.sendall(line.encode())
            except queue.Empty:
                continue
            except OSError:
                try:
                    sock.close()
                except OSError:
                    pass
                sock = None
                time.sleep(5.0)


class _NullEmitter:
    """No-op emitter when events are disabled."""

    def emit(self, signal: str, **payload: Any) -> None:
        pass


_events: _EventEmitter | _NullEmitter = (
    _EventEmitter(_EVENTS_SOCK) if _EVENTS_ENABLED else _NullEmitter()
)


# ── YAML / token helpers ─────────────────────────────────────────────


def _strip_quotes(value: str) -> str:
    """Strip paired outer quotes from scalar YAML values."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _read_mcp_yaml_scalar(key: str) -> str:
    """Read a top-level scalar from ~/.gateway/mcp.yaml without PyYAML dependency."""
    mcp_yaml = Path(
        os.environ.get("MCP_YAML", str(Path.home() / ".gateway" / "mcp.yaml"))
    ).expanduser()
    if not mcp_yaml.exists():
        return ""
    try:
        for raw_line in mcp_yaml.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if not line.startswith(f"{key}:"):
                continue
            value = line.split(":", 1)[1].split("#", 1)[0].strip()
            return _strip_quotes(value)
    except OSError as exc:
        print(
            f"mcp-stdio-proxy warning: failed reading {mcp_yaml}: {exc}",
            file=sys.stderr,
            flush=True,
        )
        return ""
    return ""


def _resolve_mcp_token() -> str:
    """Resolve MCP token from env first, then ~/.gateway/mcp.yaml auth_token."""
    from_env = os.environ.get("MCP_TOKEN", "").strip()
    if from_env:
        return from_env
    token_env_name = _read_mcp_yaml_scalar("auth_token_env").strip()
    if token_env_name:
        from_named_env = os.environ.get(token_env_name, "").strip()
        if from_named_env:
            return from_named_env
    from_yaml = _read_mcp_yaml_scalar("auth_token").strip()
    if from_yaml:
        return from_yaml
    raise RuntimeError(
        "MCP token not configured. Set MCP_TOKEN, or set auth_token_env/auth_token in ~/.gateway/mcp.yaml."
    )


# ── stdout helpers (all stdout writes go through these) ──────────────

_stdout_lock = threading.Lock()


def _write_stdout(text: str) -> None:
    """Thread-safe write to stdout."""
    with _stdout_lock:
        sys.stdout.write(text)
        sys.stdout.flush()


def _emit_proxy_error(
    *, msg_id: object | None, is_notification: bool, exc: Exception
) -> None:
    """Report a transport failure to stderr or as a JSON-RPC error response."""
    if is_notification:
        print(
            f"mcp-stdio-proxy notification error: {exc}",
            file=sys.stderr,
            flush=True,
        )
        return
    _write_stdout(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32603, "message": str(exc)},
            }
        )
        + "\n"
    )


def _emit_progress(msg_id: object, heartbeat_count: int) -> None:
    """Emit a JSON-RPC progress notification to keep the stdio pipe alive."""
    _write_stdout(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "method": "notifications/progress",
                "params": {
                    "progressToken": msg_id,
                    "progress": heartbeat_count,
                    "message": "waiting for server",
                },
            }
        )
        + "\n"
    )


# ── SSE reader (runs in worker thread) ───────────────────────────────

_HEARTBEAT = "heartbeat"
_RESULT = "result"
_ERROR = "error"

_QueueItem = (
    tuple[Literal["heartbeat"], int]
    | tuple[Literal["result"], str | None]
    | tuple[Literal["error"], Exception]
    | tuple[Literal["stream_opened"], int]
)


def _post_worker(
    body: bytes,
    *,
    token: str,
    result_queue: queue.Queue[_QueueItem],
) -> None:
    """POST body to MCP server, sending heartbeats and result via queue.

    Runs in a daemon thread.
    """
    heartbeat_count = 0
    try:
        req = urllib.request.Request(
            MCP_URL,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "Authorization": f"Bearer {token}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=_SOCKET_TIMEOUT) as resp:
            result_queue.put(("stream_opened", resp.status))
            if resp.status == 202:
                result_queue.put((_RESULT, None))
                return

            data_payload: str | None = None
            is_heartbeat = False
            while True:
                raw = resp.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                if line.startswith("event: "):
                    is_heartbeat = line[7:].strip() == "heartbeat"
                elif line.startswith("data: "):
                    if not is_heartbeat:
                        if data_payload is None:
                            data_payload = line[6:]
                        else:
                            data_payload += "\n" + line[6:]
                elif line == "":
                    if is_heartbeat:
                        heartbeat_count += 1
                        result_queue.put((_HEARTBEAT, heartbeat_count))
                    elif data_payload is not None:
                        result_queue.put((_RESULT, data_payload))
                        return
                    is_heartbeat = False
                    data_payload = None

            result_queue.put((_RESULT, data_payload or ""))

    except Exception as exc:
        result_queue.put((_ERROR, exc))


# ── Per-message handler (runs in its own thread) ─────────────────────


def _handle_message(
    raw_line: str,
    msg: dict[str, Any],
    *,
    token: str,
    inflight: threading.Semaphore,
) -> None:
    """Handle a single JSON-RPC message: HTTP round-trip + stdout response.

    Runs in a dedicated daemon thread so the stdin reader is never blocked.
    """
    is_notification = "id" not in msg
    msg_id = msg.get("id")
    mcp_method = msg.get("method")

    _events.emit(
        "mcp.transport.request.started",
        transport="stdio",
        msg_id=msg_id,
        mcp_method=mcp_method,
        is_notification=is_notification,
    )

    try:
        result = _post_with_watchdog(
            raw_line.encode("utf-8"),
            token=token,
            msg_id=msg_id,
            mcp_method=mcp_method,
        )
    except Exception as exc:
        _emit_proxy_error(
            msg_id=msg_id,
            is_notification=is_notification,
            exc=exc,
        )
        return
    finally:
        inflight.release()

    if is_notification or result is None:
        return

    if result:
        byte_len = len(result.encode("utf-8"))
        if byte_len > _LARGE_RESPONSE_BYTES:
            _events.emit(
                "mcp.transport.response.large",
                transport="stdio",
                msg_id=msg_id,
                mcp_method=mcp_method,
                response_bytes=byte_len,
                threshold_bytes=_LARGE_RESPONSE_BYTES,
            )
        _write_stdout(result + "\n")


def _post_with_watchdog(
    body: bytes,
    *,
    token: str,
    msg_id: object | None,
    mcp_method: str | None = None,
) -> str | None:
    """Run _post_worker in a thread with watchdog timeout and heartbeat forwarding."""
    rq: queue.Queue[_QueueItem] = queue.Queue()
    worker = threading.Thread(
        target=_post_worker,
        kwargs={"body": body, "token": token, "result_queue": rq},
        daemon=True,
    )
    t0 = time.monotonic()
    worker.start()

    deadline = time.monotonic() + _WATCHDOG_TIMEOUT
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            duration = time.monotonic() - t0
            _events.emit(
                "mcp.transport.request.timedout",
                transport="stdio",
                msg_id=msg_id,
                mcp_method=mcp_method,
                duration_s=round(duration, 3),
                watchdog_s=_WATCHDOG_TIMEOUT,
            )
            raise TimeoutError(f"MCP request timed out after {_WATCHDOG_TIMEOUT}s")
        try:
            kind, value = rq.get(timeout=min(_POLL_INTERVAL, remaining))
        except queue.Empty:
            continue

        if kind == _HEARTBEAT:
            if msg_id is not None:
                _emit_progress(msg_id, value)
            _events.emit(
                "mcp.transport.heartbeat.forwarded",
                transport="stdio",
                msg_id=msg_id,
                mcp_method=mcp_method,
                count=value,
            )
            deadline = time.monotonic() + _WATCHDOG_TIMEOUT
            continue
        if kind == "stream_opened":
            _events.emit(
                "mcp.transport.stream.opened",
                transport="stdio",
                msg_id=msg_id,
                mcp_method=mcp_method,
                http_status=value,
            )
            continue
        elif kind == _RESULT:
            duration = time.monotonic() - t0
            _events.emit(
                "mcp.transport.request.completed",
                transport="stdio",
                msg_id=msg_id,
                mcp_method=mcp_method,
                duration_s=round(duration, 3),
            )
            return value
        elif kind == _ERROR:
            duration = time.monotonic() - t0
            _events.emit(
                "mcp.transport.request.failed",
                transport="stdio",
                msg_id=msg_id,
                mcp_method=mcp_method,
                duration_s=round(duration, 3),
                error=str(value),
                error_type=type(value).__name__,
            )
            if isinstance(value, Exception):
                raise value
            raise RuntimeError(f"Unexpected error payload from worker: {value!r}")


# ── Main: stdin reader (always draining) ─────────────────────────────


def main() -> None:
    try:
        mcp_token = _resolve_mcp_token()
    except RuntimeError as exc:
        print(f"mcp-stdio-proxy startup error: {exc}", file=sys.stderr, flush=True)
        raise SystemExit(2) from exc

    _events.emit(
        "mcp.transport.stdio.started",
        transport="stdio",
        watchdog_s=_WATCHDOG_TIMEOUT,
        socket_s=_SOCKET_TIMEOUT,
        max_inflight=_MAX_INFLIGHT,
        mcp_url=MCP_URL,
    )
    print(
        f"mcp-stdio-proxy started pid={os.getpid()} "
        f"watchdog={_WATCHDOG_TIMEOUT}s socket={_SOCKET_TIMEOUT}s "
        f"max_inflight={_MAX_INFLIGHT}",
        file=sys.stderr,
        flush=True,
    )

    # Semaphore limits concurrent in-flight requests to prevent thread explosion.
    inflight = threading.Semaphore(_MAX_INFLIGHT)

    # stdin is read on the main thread — it NEVER blocks on HTTP.
    # Each message is dispatched to a handler thread immediately.
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as exc:
            print(
                f"mcp-stdio-proxy malformed JSON input: {exc}",
                file=sys.stderr,
                flush=True,
            )
            continue

        # Acquire semaphore before spawning — if all slots are busy,
        # this blocks the stdin reader briefly (back-pressure).
        inflight.acquire()

        handler = threading.Thread(
            target=_handle_message,
            args=(line, msg),
            kwargs={"token": mcp_token, "inflight": inflight},
            daemon=True,
        )
        handler.start()


if __name__ == "__main__":
    main()
