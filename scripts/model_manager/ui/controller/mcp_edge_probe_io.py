"""Sockets for the public MCP edge probe: DNS, TLS, and the wait-call query.

The manage loop calls these. DNS skips the resolved stub, which answers
``mcp.k-1.me`` from ``/etc/hosts``, and asks an uplink resolver instead.
TLS uses that public address with the public name as SNI. The wait-call
query is the event-service read the listener-silence check classifies.
"""

from __future__ import annotations

import os
import socket
import ssl
import subprocess
from pathlib import Path

from deploy_identity.mcp_external_probe import (
    HEALTH_PATH,
    build_dns_query,
    choose_public_ipv4,
    http_status_from_bytes,
    json_object_from_mixed_stdout,
    parse_dns_a_records,
    probe_nameservers,
)

_DNS_TIMEOUT_S = 3.0
_TLS_TIMEOUT_S = 8.0
_FALLBACK_NAMESERVER = "1.1.1.1"

_WAIT_SQL = """
SELECT json_extract(payload, '$.thread') AS thread,
       COUNT(*) AS n,
       MIN(ts_unix_ms) AS first_ms,
       MAX(ts_unix_ms) AS last_ms
FROM events
WHERE signal = 'mcp.agentbus.wait.called'
  AND ts_unix_ms >= ?
GROUP BY thread
"""


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def resolve_a_records(
    host: str,
    *,
    resolv_text: str | None = None,
    uplink_text: str | None = None,
) -> list[str]:
    """Query an uplink nameserver for ``host`` A records.

    Skips the systemd-resolved stub. A packet that contains only loopback
    or private addresses is not the answer: the next nameserver is tried.
    Raises ``TimeoutError`` or ``OSError`` when no nameserver answers.
    """
    read_disk = resolv_text is None and uplink_text is None
    if resolv_text is None:
        resolv_text = _read_text(Path("/etc/resolv.conf"))
    if uplink_text is None:
        uplink_path = Path("/run/systemd/resolve/resolv.conf")
        uplink_text = _read_text(uplink_path) if read_disk else ""
    servers = probe_nameservers(resolv_text, uplink_text) or [_FALLBACK_NAMESERVER]
    query = build_dns_query(host)
    last_error: BaseException | None = None
    last_records: list[str] = []
    saw_answer = False
    for server in servers:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.settimeout(_DNS_TIMEOUT_S)
            sock.sendto(query, (server, 53))
            packet, _peer = sock.recvfrom(2048)
        except OSError as exc:
            last_error = exc
            continue
        finally:
            sock.close()
        records = parse_dns_a_records(packet)
        saw_answer = True
        last_records = records
        if choose_public_ipv4(records):
            return records
    if last_error is not None and not saw_answer:
        raise last_error
    return last_records


def fetch_health(ip: str, host: str) -> int:
    """GET ``/health`` on ``ip:443`` with SNI and Host set to ``host``.

    Returns the HTTP status code. TLS EOF and connect resets raise.
    Certificate check uses ``host``, so a hairpin to the public address
    still validates the public certificate. ``suppress_ragged_eofs`` is
    off so an EOF after the handshake raises ``SSLEOFError`` (``tls_eof``)
    instead of coming back from ``recv`` as an empty buffer.
    """
    ctx = ssl.create_default_context()
    raw = socket.create_connection((ip, 443), timeout=_TLS_TIMEOUT_S)
    try:
        tls = ctx.wrap_socket(raw, server_hostname=host, suppress_ragged_eofs=False)
    except BaseException:
        raw.close()
        raise
    try:
        request = (
            f"GET {HEALTH_PATH} HTTP/1.1\r\n"
            f"Host: {host}\r\n"
            "Connection: close\r\n"
            "User-Agent: ulg-mcp-edge-probe\r\n"
            "\r\n"
        )
        tls.sendall(request.encode("ascii"))
        buf = b""
        while b"\r\n" not in buf and len(buf) < 512:
            chunk = tls.recv(256)
            if not chunk:
                break
            buf += chunk
    finally:
        tls.close()
    return http_status_from_bytes(buf)


def query_wait_rows(workspace_root: Path, *, floor_ms: int) -> list[dict[str, object]]:
    """Return grouped ``mcp.agentbus.wait.called`` rows since ``floor_ms``.

    Raises ``RuntimeError`` when ``query-events`` is missing or exits
    non-zero. An empty list means the window had no wait calls.
    """
    script = workspace_root / "scripts" / "query-events"
    if not script.is_file():
        raise RuntimeError(f"query-events not found: {script}")
    cmd = [
        str(script),
        "--sql",
        _WAIT_SQL,
        "--sql-param",
        str(floor_ms),
        "--limit",
        "10000",
        "--compact",
    ]
    socket_path = os.environ.get("EVENTS_QUERY_SOCK")
    if socket_path:
        cmd.extend(["--socket", socket_path])
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        err = proc.stderr.strip() or proc.stdout.strip() or "query-events failed"
        raise RuntimeError(err)
    payload = json_object_from_mixed_stdout(proc.stdout or "")
    rows = payload.get("rows")
    if isinstance(rows, list):
        return rows
    return []


__all__ = ["fetch_health", "query_wait_rows", "resolve_a_records"]
