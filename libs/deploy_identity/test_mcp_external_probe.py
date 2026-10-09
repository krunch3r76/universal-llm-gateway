"""External-vantage MCP probe decisions and listener-silence classification."""

from __future__ import annotations

import socket
import ssl
import struct

import pytest

from deploy_identity.listener_liveness import silent_listeners
from deploy_identity.mcp_edge_events import (
    mcp_edge_probe_failed,
    mcp_edge_probe_succeeded,
)
from deploy_identity.mcp_external_probe import (
    assess_external_probe,
    build_dns_query,
    choose_public_ipv4,
    classify_probe_error,
    external_probe_hostname,
    http_status_from_bytes,
    nameservers_from_resolv,
    parse_dns_a_records,
)

_PUBLIC = "73.189.160.136"


def _a_response(ip: str, name: str = "mcp.k-1.me") -> bytes:
    query = build_dns_query(name, txn_id=7)
    header = struct.pack(">HHHHHH", 7, 0x8180, 1, 1, 0, 0)
    answer = struct.pack(">H", 0xC00C) + struct.pack(">HHIH", 1, 1, 60, 4)
    answer += socket.inet_aton(ip)
    return header + query[12:] + answer


def test_hostname_skips_loopback_and_defaults(monkeypatch):
    monkeypatch.setenv("MCP_EDGE_PROBE_HOST", "127.0.0.1")
    monkeypatch.setenv("MCP_PUBLIC_URL", "https://mcp.k-1.me/mcp/code/")
    assert external_probe_hostname() == "mcp.k-1.me"


def test_hostname_reads_public_url_host(monkeypatch):
    monkeypatch.delenv("MCP_EDGE_PROBE_HOST", raising=False)
    monkeypatch.setenv("MCP_PUBLIC_URL", "https://mcp.k-1.me/mcp/code/")
    monkeypatch.delenv("MCP_SERVER_URL", raising=False)
    assert external_probe_hostname() == "mcp.k-1.me"


def test_nameservers_keep_ipv4_only():
    text = "nameserver 127.0.0.53\nnameserver 2001:db8::1\n# comment\n"
    assert nameservers_from_resolv(text) == ["127.0.0.53"]


def test_dns_round_trip_parses_public_a():
    packet = _a_response(_PUBLIC)
    assert parse_dns_a_records(packet) == [_PUBLIC]


def test_truncated_dns_packet_raises():
    with pytest.raises(ValueError):
        parse_dns_a_records(b"\x00\x01")


def test_choose_public_ip_rejects_loopback_and_lan():
    assert choose_public_ipv4(["127.0.0.1", "10.0.0.67", _PUBLIC]) == _PUBLIC
    assert choose_public_ipv4(["127.0.0.1", "10.0.0.67"]) is None


def test_http_200_on_public_ip_succeeds():
    result = assess_external_probe(
        host="mcp.k-1.me",
        addresses=[_PUBLIC],
        http_status=200,
        error_class=None,
        detail="",
    )
    assert result.status == "succeeded"
    assert result.ip == _PUBLIC
    assert result.http_status == 200


def test_loopback_only_answer_is_not_healthy():
    result = assess_external_probe(
        host="mcp.k-1.me",
        addresses=["127.0.0.1"],
        http_status=200,
        error_class=None,
        detail="",
    )
    assert result.status == "failed"
    assert result.error_class == "no_public_address"


def test_tls_eof_is_failure_even_with_public_ip():
    result = assess_external_probe(
        host="mcp.k-1.me",
        addresses=[_PUBLIC],
        http_status=None,
        error_class="tls_eof",
        detail="EOF occurred in violation of protocol",
    )
    assert result.status == "failed"
    assert result.error_class == "tls_eof"
    assert result.ip == _PUBLIC


def test_dns_timeout_does_not_look_healthy():
    result = assess_external_probe(
        host="mcp.k-1.me",
        addresses=[],
        http_status=None,
        error_class="timeout",
        detail="timed out",
    )
    assert result.status == "failed"
    assert result.ip is None
    assert result.error_class == "timeout"


def test_classify_ssl_eof_and_timeout():
    assert classify_probe_error(ssl.SSLEOFError("EOF")) == "tls_eof"
    assert classify_probe_error(TimeoutError("timed out")) == "timeout"
    assert classify_probe_error(ConnectionResetError()) == "connect_reset"


def test_http_status_line():
    assert http_status_from_bytes(b"HTTP/1.1 200 OK\r\n\r\n") == 200
    with pytest.raises(ValueError):
        http_status_from_bytes(b"")


def test_events_use_health_path_not_mount_path():
    ok = mcp_edge_probe_succeeded(host="mcp.k-1.me", ip=_PUBLIC, http_status=200)
    bad = mcp_edge_probe_failed(
        host="mcp.k-1.me",
        ip=None,
        error_class="no_public_address",
        detail="hosts file",
    )
    assert ok.signal == "mcp.edge.probe.succeeded"
    assert ok.payload["path"] == "/health"
    assert bad.signal == "mcp.edge.probe.failed"
    assert bad.scope == "node"


def test_standing_listener_silent_after_gap():
    now = 1_000_000_000_000
    rows = [
        {
            "thread": "15851",
            "n": 40,
            "first_ms": now - 90 * 60 * 1000,
            "last_ms": now - 15 * 60 * 1000,
        }
    ]
    found = silent_listeners(rows, now_ms=now)
    assert found == [
        {
            "thread": "15851",
            "calls": 40,
            "last_ms": now - 15 * 60 * 1000,
            "age_ms": 15 * 60 * 1000,
        }
    ]


def test_fresh_wait_and_short_burst_are_quiet():
    now = 1_000_000_000_000
    healthy = {
        "thread": "15609",
        "n": 100,
        "first_ms": now - 60 * 60 * 1000,
        "last_ms": now - 10_000,
    }
    burst = {
        "thread": "99999",
        "n": 20,
        "first_ms": now - 5 * 60 * 1000,
        "last_ms": now - 20 * 60 * 1000,
    }
    assert silent_listeners([healthy, burst], now_ms=now) == []


def test_non_200_is_bad_status():
    result = assess_external_probe(
        host="mcp.k-1.me",
        addresses=[_PUBLIC],
        http_status=503,
        error_class=None,
        detail="",
    )
    assert result.status == "failed"
    assert result.error_class == "bad_status"
    assert result.http_status == 503
