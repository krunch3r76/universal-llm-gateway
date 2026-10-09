"""Sockets for the public MCP edge probe: DNS, TLS, and the wait-call query.

The manage loop calls these. DNS goes to a resolv.conf nameserver so the
hosts file cannot answer ``mcp.k-1.me`` with ``127.0.0.1``. TLS uses that
public address with the public name as SNI. The wait-call query is the
event-service read the listener-silence check classifies.
"""

from __future__ import annotations

import json
import os
import socket
import ssl
import subprocess
from pathlib import Path

from deploy_identity.mcp_external_probe import (
    HEALTH_PATH,
    build_dns_query,
    http_status_from_bytes,
    nameservers_from_resolv,
    parse_dns_a_records,
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


def resolve_a_records(host: str, *, resolv_text: str | None = None) -> list[str]:
    """Query the first resolv.conf nameserver for ``host`` A records.

    Uses DNS, not ``getaddrinfo``, so ``/etc/hosts`` cannot substitute
    ``127.0.0.1``. Raises ``TimeoutError`` or ``OSError`` when no nameserver
    answers. Returns the parsed A list, which may be empty.
    """
    text = resolv_text
    if text is None:
        try:
            text = Path("/etc/resolv.conf").read_text(encoding="utf-8")
        except OSError:
            text = ""
    servers = nameservers_from_resolv(text) or [_FALLBACK_NAMESERVER]
    query = build_dns_query(host)
    last_error: BaseException | None = None
    for server in servers[:2]:
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
        return parse_dns_a_records(packet)
    if last_error is not None:
        raise last_error
    return []


def fetch_health(ip: str, host: str) -> int:
    """GET ``/health`` on ``ip:443`` with SNI and Host set to ``host``.

    Returns the HTTP status code. TLS EOF and connect resets raise.
    Certificate check uses ``host``, so a hairpin to the public address
    still validates the public certificate.
    """
    ctx = ssl.create_default_context()
    raw = socket.create_connection((ip, 443), timeout=_TLS_TIMEOUT_S)
    try:
        tls = ctx.wrap_socket(raw, server_hostname=host)
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
    payload = json.loads(proc.stdout or "{}")
    rows = payload.get("rows")
    if isinstance(rows, list):
        return rows
    return []


__all__ = ["fetch_health", "query_wait_rows", "resolve_a_records"]
