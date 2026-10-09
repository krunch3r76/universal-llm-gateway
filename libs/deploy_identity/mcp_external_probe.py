"""Public-vantage MCP health decision, without opening a socket.

Manage's edge-probe loop calls these helpers. The MCP container healthcheck
hits ``https://localhost/health`` from inside the container, and io's hosts
file maps ``mcp.k-1.me`` to ``127.0.0.1``, so a hostname GET from this host
is loopback. Callers resolve an A record over DNS (``/etc/resolv.conf``,
which does not consult the hosts file) and connect only to a globally
routable IPv4 address.

``/health`` is the path AuthMiddleware short-circuits. ``/mcp/code/health``
is not that route; probing it reports a false outage while TLS is fine.
"""

from __future__ import annotations

import os
import socket
import struct
from dataclasses import dataclass
from urllib.parse import urlparse

_DEFAULT_HOST = "mcp.k-1.me"
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
HEALTH_PATH = "/health"


def external_probe_hostname() -> str:
    """Return the public MCP hostname, never a loopback name.

    ``MCP_EDGE_PROBE_HOST`` wins, then the hostname of ``MCP_PUBLIC_URL`` or
    ``MCP_SERVER_URL``. A loopback or empty value is skipped. The default is
    ``mcp.k-1.me`` when nothing usable is set. Does not read ``/etc/hosts``.
    """
    for key in ("MCP_EDGE_PROBE_HOST", "MCP_PUBLIC_URL", "MCP_SERVER_URL"):
        raw = os.environ.get(key, "").strip()
        if not raw:
            continue
        host = raw
        if "://" in raw:
            host = urlparse(raw).hostname or ""
        host = host.strip().lower().rstrip(".")
        if not host or host in _LOOPBACK_HOSTS:
            continue
        return host
    return _DEFAULT_HOST


def nameservers_from_resolv(text: str) -> list[str]:
    """Return IPv4 ``nameserver`` addresses from resolv.conf text, in file order."""
    found: list[str] = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 2 or parts[0] != "nameserver":
            continue
        candidate = parts[1]
        try:
            packed = socket.inet_aton(candidate)
        except OSError:
            continue
        found.append(socket.inet_ntoa(packed))
    return found


def build_dns_query(hostname: str, txn_id: int = 1) -> bytes:
    """Build a DNS A query for ``hostname`` (recursion desired, one question)."""
    if not 0 <= txn_id <= 0xFFFF:
        raise ValueError("txn_id must fit in 16 bits")
    labels = hostname.strip().rstrip(".").split(".")
    if not labels or any(not label or len(label) > 63 for label in labels):
        raise ValueError("hostname is not a DNS name")
    qname = b"".join(bytes([len(label)]) + label.encode("ascii") for label in labels)
    qname += b"\x00"
    header = struct.pack(">HHHHHH", txn_id, 0x0100, 1, 0, 0, 0)
    return header + qname + struct.pack(">HH", 1, 1)


def _skip_dns_name(packet: bytes, offset: int) -> int:
    while True:
        if offset >= len(packet):
            raise ValueError("truncated DNS name")
        length = packet[offset]
        if length == 0:
            return offset + 1
        if length & 0xC0 == 0xC0:
            if offset + 1 >= len(packet):
                raise ValueError("truncated DNS compression pointer")
            return offset + 2
        offset += 1 + length


def parse_dns_a_records(packet: bytes) -> list[str]:
    """Return IPv4 A-record addresses from a DNS response, in answer order.

    Raises ``ValueError`` when the packet is truncated. Non-A answers are
    skipped. An empty list means the response carried no A data.
    """
    if len(packet) < 12:
        raise ValueError("DNS packet shorter than a header")
    _txn, _flags, qdcount, ancount, _ns, _ar = struct.unpack(">HHHHHH", packet[:12])
    offset = 12
    for _ in range(qdcount):
        offset = _skip_dns_name(packet, offset)
        if offset + 4 > len(packet):
            raise ValueError("truncated DNS question")
        offset += 4
    records: list[str] = []
    for _ in range(ancount):
        offset = _skip_dns_name(packet, offset)
        if offset + 10 > len(packet):
            raise ValueError("truncated DNS answer")
        rtype, _rclass, _ttl, rdlen = struct.unpack(
            ">HHIH", packet[offset : offset + 10]
        )
        offset += 10
        if offset + rdlen > len(packet):
            raise ValueError("truncated DNS rdata")
        rdata = packet[offset : offset + rdlen]
        offset += rdlen
        if rtype == 1 and rdlen == 4:
            records.append(socket.inet_ntoa(rdata))
    return records


def choose_public_ipv4(addresses: list[str]) -> str | None:
    """Return the first globally routable IPv4 address, or None.

    Loopback, private, link-local, and unparsable values are skipped. That
    drops io's hosts-file ``127.0.0.1`` and a split-horizon LAN address such
    as ``10.0.0.67``, which are not the WAN hairpin clients use.
    """
    import ipaddress

    for raw in addresses:
        try:
            ip = ipaddress.ip_address(raw.strip())
        except ValueError:
            continue
        if not isinstance(ip, ipaddress.IPv4Address):
            continue
        if ip.is_global:
            return str(ip)
    return None


@dataclass(frozen=True)
class ProbeResult:
    """One external-vantage decision. ``status`` is ``succeeded`` or ``failed``."""

    status: str
    host: str
    ip: str | None
    http_status: int | None
    error_class: str | None
    detail: str


def http_status_from_bytes(buf: bytes) -> int:
    """Parse the status code from the first line of an HTTP response.

    Raises ``ValueError`` when the bytes are not an HTTP status line, which
    is how a TLS EOF that returns an empty buffer is kept out of ``bad_status``.
    """
    line = buf.split(b"\r\n", 1)[0].decode("latin1", errors="replace")
    parts = line.split()
    if len(parts) < 2 or not parts[0].startswith("HTTP/"):
        raise ValueError("not an HTTP status line")
    return int(parts[1])


def classify_probe_error(exc: BaseException) -> str:
    """Map a probe exception to a stable ``error_class`` token.

    ``tls_eof`` covers ``SSLEOFError`` and SSL messages that name EOF, which
    is the client-visible shape of a relay that accepts TCP and resets.
    Timeouts are classified before the generic ``OSError`` branch.
    """
    import ssl

    if isinstance(exc, ssl.SSLEOFError):
        return "tls_eof"
    if isinstance(exc, TimeoutError):
        return "timeout"
    if isinstance(exc, ssl.SSLError):
        text = str(exc).upper()
        if "EOF" in text:
            return "tls_eof"
        return "tls_error"
    if isinstance(exc, ConnectionResetError):
        return "connect_reset"
    if isinstance(exc, ConnectionRefusedError):
        return "connect_refused"
    if isinstance(exc, OSError):
        return "os_error"
    return "probe_error"


def assess_external_probe(
    *,
    host: str,
    addresses: list[str],
    http_status: int | None,
    error_class: str | None,
    detail: str,
) -> ProbeResult:
    """Decide probe success from a DNS answer list and an HTTP result.

    A DNS or connect failure is ``failed`` and does not pretend the mount is
    healthy. HTTP 200 on a public IPv4 address is the only success. Any other
    status, or no globally routable address, is ``failed``.
    """
    trimmed = detail[:200]
    if error_class and not addresses:
        return ProbeResult(
            status="failed",
            host=host,
            ip=None,
            http_status=None,
            error_class=error_class,
            detail=trimmed,
        )
    ip = choose_public_ipv4(addresses)
    if ip is None:
        return ProbeResult(
            status="failed",
            host=host,
            ip=None,
            http_status=None,
            error_class="no_public_address",
            detail=trimmed or "no globally routable A record",
        )
    if error_class:
        return ProbeResult(
            status="failed",
            host=host,
            ip=ip,
            http_status=http_status,
            error_class=error_class,
            detail=trimmed,
        )
    if http_status == 200:
        return ProbeResult(
            status="succeeded",
            host=host,
            ip=ip,
            http_status=200,
            error_class=None,
            detail=trimmed,
        )
    return ProbeResult(
        status="failed",
        host=host,
        ip=ip,
        http_status=http_status,
        error_class="bad_status",
        detail=trimmed or f"HTTP {http_status}",
    )


__all__ = [
    "HEALTH_PATH",
    "ProbeResult",
    "assess_external_probe",
    "build_dns_query",
    "choose_public_ipv4",
    "classify_probe_error",
    "external_probe_hostname",
    "http_status_from_bytes",
    "nameservers_from_resolv",
    "parse_dns_a_records",
]
