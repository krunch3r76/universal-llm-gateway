"""Gateway forwarder binds only 172.18.0.1 and does not dial CDP to decide."""

from __future__ import annotations

import socket
from pathlib import Path

import pytest
import yaml

from cursor_bridge.cdp_gateway_forwarder import (
    _handle_accepted_client,
    _relay_client,
    allowed_cdp_peers,
    bind_targets,
    container_cdp_origin,
    is_allowed_cdp_peer,
    local_ipv4_addresses_from_trie,
    mcp_server_peers_from_inspect,
    on_docker_bridge,
    open_listeners,
)
from cursor_bridge.cursor_window_config import cdp_origin_for_gui_host
from cursor_bridge.wake_delivery import normalize_target

pytestmark = pytest.mark.offline

_COMPOSE = Path(__file__).resolve().parents[2] / "docker" / "compose" / "mcp-server.yml"
_TRIE = """
Main:
  +-- 0.0.0.0/0 3 0 5
     |-- 127.0.0.1
        /32 host LOCAL
     |-- 172.18.0.0
        /16 link UNICAST
     |-- 172.18.0.1
        /32 host LOCAL
     |-- 10.0.0.67
        /32 host LOCAL
"""


class _FakeSocket:
    def __init__(self) -> None:
        self.bound: tuple[str, int] | None = None
        self.listening: int | None = None
        self.closed = False
        self.opts: list[tuple[int, int, int]] = []

    def setsockopt(self, level: int, name: int, value: int) -> None:
        self.opts.append((level, name, value))

    def bind(self, addr: tuple[str, int]) -> None:
        self.bound = addr

    def listen(self, backlog: int) -> None:
        self.listening = backlog

    def close(self) -> None:
        self.closed = True

    def getsockname(self) -> tuple[str, int]:
        assert self.bound is not None
        return self.bound


class _BusySecond(_FakeSocket):
    def bind(self, addr: tuple[str, int]) -> None:
        if addr[1] == 9339:
            raise OSError("address in use")
        self.bound = addr


def test_trie_parser_keeps_host_local_32_only() -> None:
    found = local_ipv4_addresses_from_trie(_TRIE)
    assert found == frozenset({"127.0.0.1", "172.18.0.1", "10.0.0.67"})
    assert on_docker_bridge(found) is False


def test_bridge_client_is_not_the_gateway_address() -> None:
    assert on_docker_bridge(frozenset({"172.18.0.5"})) is True
    assert on_docker_bridge(frozenset({"172.18.0.1"})) is False
    assert on_docker_bridge(frozenset({"172.17.0.2", "10.0.0.67"})) is False


def test_off_bridge_probe_does_not_open_cdp_ports(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("probe opened a socket")

    monkeypatch.setattr(socket, "socket", boom)
    monkeypatch.setattr(socket, "create_connection", boom)
    host = frozenset({"127.0.0.1", "172.18.0.1", "10.0.0.67"})
    assert container_cdp_origin("http://127.0.0.1:9338", addresses=host) == (
        "http://127.0.0.1:9338"
    )
    assert container_cdp_origin("http://127.0.0.1:9339", addresses=host) == (
        "http://127.0.0.1:9339"
    )
    assert bind_targets(host) == (("172.18.0.1", 9338), ("172.18.0.1", 9339))
    assert bind_targets(frozenset({"172.18.0.5", "10.0.0.67"})) == ()
    monkeypatch.setattr(
        "cursor_bridge.cdp_gateway_forwarder.local_ipv4_addresses",
        lambda: host,
    )
    assert normalize_target("8", "cursor")["origin"] == "http://127.0.0.1:9338"
    assert cdp_origin_for_gui_host(":8") == "http://127.0.0.1:9338"


def test_open_listeners_skips_when_gateway_is_absent() -> None:
    created: list[_FakeSocket] = []

    def factory(*_args: object, **_kwargs: object) -> _FakeSocket:
        sock = _FakeSocket()
        created.append(sock)
        return sock

    assert open_listeners(frozenset({"172.18.0.5"}), socket_factory=factory) == []
    assert created == []


def test_open_listeners_binds_gateway_only() -> None:
    created: list[_FakeSocket] = []

    def factory(*_args: object, **_kwargs: object) -> _FakeSocket:
        sock = _FakeSocket()
        created.append(sock)
        return sock

    listeners = open_listeners(
        frozenset({"172.18.0.1", "127.0.0.1"}), socket_factory=factory
    )
    assert [sock.bound for sock in listeners] == [
        ("172.18.0.1", 9338),
        ("172.18.0.1", 9339),
    ]
    assert all(sock.listening == 16 for sock in listeners)
    assert "0.0.0.0" not in {
        addr[0] for sock in created if sock.bound for addr in [sock.bound]
    }


def test_bind_failure_closes_listener_already_opened() -> None:
    created: list[_BusySecond] = []

    def factory(*_args: object, **_kwargs: object) -> _BusySecond:
        sock = _BusySecond()
        created.append(sock)
        return sock

    with pytest.raises(OSError, match="address in use"):
        open_listeners(frozenset({"172.18.0.1"}), socket_factory=factory)
    assert created[0].bound == ("172.18.0.1", 9338)
    assert created[0].closed is True
    assert len(created) == 2


def test_allowed_peers_only_mcp_server_container() -> None:
    mcp = frozenset({"172.18.0.5"})
    other = frozenset({"172.18.0.12"})
    assert is_allowed_cdp_peer("172.18.0.5", allowed=mcp)
    assert not is_allowed_cdp_peer("172.18.0.12", allowed=other)
    assert mcp_server_peers_from_inspect("172.18.0.5 172.17.0.2 ") == frozenset(
        {"172.18.0.5", "172.17.0.2"}
    )
    assert allowed_cdp_peers(inspect_output="172.18.0.5 ") == frozenset({"172.18.0.5"})


def test_refuses_non_mcp_compose_peer_before_relay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Client:
        def __init__(self, host: str) -> None:
            self.host = host
            self.closed = False
            self.relayed = False

        def getpeername(self) -> tuple[str, int]:
            return (self.host, 40000)

        def close(self) -> None:
            self.closed = True

    def boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("relay must not run for a refused peer")

    monkeypatch.setattr(
        "cursor_bridge.cdp_gateway_forwarder._relay_client",
        boom,
    )
    allowed = frozenset({"172.18.0.5"})
    bad = _Client("172.18.0.12")
    _handle_accepted_client(bad, 9338, allowed=allowed)
    assert bad.closed is True


def test_upstream_down_closes_client_without_raising() -> None:
    class _Client:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    client = _Client()

    def fail_connect(*_args: object, **_kwargs: object) -> socket.socket:
        raise OSError("connection refused")

    _relay_client(client, 9338, connect=fail_connect)  # type: ignore[arg-type]
    assert client.closed is True


def test_compose_publishes_gateway_forwarder_not_mcp_host_network() -> None:
    loaded = yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))
    services = loaded["services"]
    mcp = services["mcp-server"]
    forwarder = services["cdp-gateway-forwarder"]
    assert "network_mode" not in mcp
    assert forwarder["network_mode"] == "host"
    assert "ports" not in forwarder
    text = _COMPOSE.read_text(encoding="utf-8")
    assert "0.0.0.0:9338" not in text
    assert "0.0.0.0:9339" not in text
    assert forwarder["command"] == [
        "python",
        "-m",
        "cursor_bridge.cdp_gateway_forwarder",
    ]
