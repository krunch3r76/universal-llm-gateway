"""Gateway-only TCP forwarder for host Cursor and Grok CDP ports.

The mcp-server container cannot open ``127.0.0.1:9338`` or ``:9339`` because
that loopback is the container, not the host where Cursor and grok-bot listen.
This module is the container-side origin rewrite and the host-side listener
that compose runs as ``cdp-gateway-forwarder``. Host and Stargate callers stay
on loopback. A process that does not own ``172.18.0.1`` does not bind those
ports. Membership is read from local addresses; nothing here connects to
9338 or 9339 to decide.
"""

from __future__ import annotations

import ipaddress
import os
import socket
import subprocess
import threading
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit

from universal_logging import get_logger

logger = get_logger(__name__)

GATEWAY_HOST = "172.18.0.1"
LOOPBACK_HOST = "127.0.0.1"
FORWARDED_PORTS = (9338, 9339)
_BRIDGE = ipaddress.ip_network("172.18.0.0/16")
_GATEWAY = ipaddress.ip_address(GATEWAY_HOST)
_TRIE = Path("/proc/net/fib_trie")
_MCP_CONTAINER = os.environ.get("CDP_GATEWAY_MCP_CONTAINER", "mcp-server")
_ALLOWED_PEERS_ENV = "CDP_GATEWAY_ALLOWED_PEERS"


def local_ipv4_addresses_from_trie(text: str) -> frozenset[str]:
    """Return IPv4 addresses marked ``/32 host LOCAL`` in one fib_trie dump.

    The address sits on the ``|--`` line above the ``/32 host LOCAL`` marker.
    Link and broadcast rows are ignored. No sockets are opened.
    """
    found: set[str] = set()
    pending: str | None = None
    for raw in text.splitlines():
        stripped = raw.strip()
        if stripped.startswith("|--") or stripped.startswith("+--"):
            host = stripped.split()[-1].split("/")[0]
            pending = host if host.count(".") == 3 else None
            continue
        if pending and stripped.startswith("/32 host LOCAL"):
            found.add(pending)
        pending = None
    return frozenset(found)


def local_ipv4_addresses() -> frozenset[str]:
    """Local IPv4 addresses from ``/proc/net/fib_trie``, or empty if unreadable.

    Used to tell a docker-bridge client from the host that owns the gateway.
    Does not connect to the CDP ports.
    """
    try:
        text = _TRIE.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return frozenset()
    return local_ipv4_addresses_from_trie(text)


def mcp_server_peers_from_inspect(text: str) -> frozenset[str]:
    """Parse ``docker inspect`` IPAddress template output into peer hosts."""
    return frozenset(
        item
        for item in text.split()
        if item.count(".") == 3 and not item.startswith("127.")
    )


def allowed_cdp_peers(*, inspect_output: str | None = None) -> frozenset[str]:
    """Addresses allowed to connect to the gateway forwarder listeners.

    ``CDP_GATEWAY_ALLOWED_PEERS`` wins when set. Otherwise the mcp-server
    container IP is read from ``docker inspect``. An empty set means fail
    closed: every peer is refused until configuration resolves.
    """
    env = os.environ.get(_ALLOWED_PEERS_ENV, "").strip()
    if env:
        return frozenset(item.strip() for item in env.split(",") if item.strip())
    if inspect_output is not None:
        return mcp_server_peers_from_inspect(inspect_output)
    try:
        proc = subprocess.run(
            [
                "docker",
                "inspect",
                "-f",
                "{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}",
                _MCP_CONTAINER,
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return frozenset()
    if proc.returncode != 0:
        return frozenset()
    return mcp_server_peers_from_inspect(proc.stdout)


def is_allowed_cdp_peer(
    peer_host: str, *, allowed: frozenset[str] | None = None
) -> bool:
    """True only when ``peer_host`` is the mcp-server container on compose_default."""
    peers = allowed_cdp_peers() if allowed is None else allowed
    if not peers:
        return False
    return peer_host in peers


def on_docker_bridge(addresses: frozenset[str]) -> bool:
    """True when ``addresses`` contains a non-gateway host on ``172.18.0.0/16``.

    ``172.18.0.1`` is the bridge gateway on the docker host, not a container.
    Host and Stargate processes therefore stay off this predicate.
    """
    for item in addresses:
        try:
            addr = ipaddress.ip_address(item)
        except ValueError:
            continue
        if addr in _BRIDGE and addr != _GATEWAY:
            return True
    return False


def _gateway_origin(origin: str) -> str | None:
    parts = urlsplit(origin)
    if parts.hostname != LOOPBACK_HOST or parts.port not in FORWARDED_PORTS:
        return None
    if not parts.scheme:
        return None
    return f"{parts.scheme}://{GATEWAY_HOST}:{parts.port}"


def container_cdp_origin(
    origin: str, *, addresses: frozenset[str] | None = None
) -> str:
    """Rewrite loopback 9338/9339 to the gateway only for a bridge client.

    Any other origin, including orion-node ``19338`` and grok display 10
    ``9340``, is returned unchanged. When ``addresses`` is omitted the local
    fib_trie is read. This function does not open 9338 or 9339.
    """
    rewritten = _gateway_origin(origin)
    if rewritten is None:
        return origin
    addrs = local_ipv4_addresses() if addresses is None else addresses
    if not on_docker_bridge(addrs):
        return origin
    return rewritten


def bind_targets(addresses: frozenset[str]) -> tuple[tuple[str, int], ...]:
    """Listener addresses for the forwarder, or empty when the gateway is absent.

    Only ``172.18.0.1`` on 9338 and 9339. An empty result means the caller
    must not create those sockets. ``0.0.0.0`` is never a target.
    """
    if GATEWAY_HOST not in addresses:
        return ()
    return tuple((GATEWAY_HOST, port) for port in FORWARDED_PORTS)


def open_listeners(
    addresses: frozenset[str],
    *,
    socket_factory: Callable[..., socket.socket] = socket.socket,
) -> list[socket.socket]:
    """Bind the gateway listeners, or return nothing if this host is not the gateway.

    A failed bind closes any listener already opened and raises. The bind
    address is never rewritten to ``0.0.0.0`` or loopback.
    """
    opened: list[socket.socket] = []
    try:
        for host, port in bind_targets(addresses):
            sock = socket_factory(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((host, port))
            sock.listen(16)
            opened.append(sock)
    except OSError:
        for sock in opened:
            sock.close()
        raise
    return opened


def _pump(src: socket.socket, dst: socket.socket) -> None:
    try:
        while True:
            chunk = src.recv(65536)
            if not chunk:
                break
            dst.sendall(chunk)
    except OSError:
        return
    finally:
        for sock in (src, dst):
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def _relay_client(
    client: socket.socket,
    port: int,
    *,
    connect: Callable[..., socket.socket] = socket.create_connection,
) -> None:
    """Forward one accepted client to host loopback ``port``.

    When the host CDP port is down, the client is closed and the listener
    stays up. The upstream target is always ``127.0.0.1``, never the gateway.
    """
    try:
        upstream = connect((LOOPBACK_HOST, port), 5.0)
    except OSError:
        client.close()
        return
    left = threading.Thread(target=_pump, args=(client, upstream), daemon=True)
    right = threading.Thread(target=_pump, args=(upstream, client), daemon=True)
    left.start()
    right.start()
    left.join()
    right.join()
    client.close()
    upstream.close()


def _handle_accepted_client(
    client: socket.socket,
    port: int,
    *,
    allowed: frozenset[str] | None = None,
) -> None:
    try:
        peer_host = client.getpeername()[0]
    except OSError:
        client.close()
        return
    if not is_allowed_cdp_peer(peer_host, allowed=allowed):
        logger.warning(
            "cdp gateway forwarder: refused peer %s on port %s", peer_host, port
        )
        client.close()
        return
    threading.Thread(
        target=_relay_client,
        args=(client, port),
        name=f"cdp-fwd-{port}",
        daemon=True,
    ).start()


def _accept_loop(
    listener: socket.socket,
    port: int,
    *,
    allowed: frozenset[str] | None = None,
) -> None:
    peers = allowed if allowed is not None else allowed_cdp_peers()
    while True:
        try:
            client, _addr = listener.accept()
        except OSError:
            return
        _handle_accepted_client(client, port, allowed=peers)


def main() -> None:
    """Listen on the docker gateway, or wait without binding when it is absent.

    The wait path is for a process that is not the bridge host. Exiting would
    restart-loop under compose and must not be how a missing gateway is handled.
    """
    listeners = open_listeners(local_ipv4_addresses())
    if not listeners:
        logger.error(
            "cdp gateway forwarder: %s is not local; not binding %s",
            GATEWAY_HOST,
            ",".join(str(port) for port in FORWARDED_PORTS),
        )
        threading.Event().wait()
        return
    threads: list[threading.Thread] = []
    for sock in listeners:
        port = int(sock.getsockname()[1])
        thread = threading.Thread(
            target=_accept_loop,
            args=(sock, port),
            name=f"cdp-fwd-accept-{port}",
            daemon=True,
        )
        thread.start()
        threads.append(thread)
    logger.info(
        "cdp gateway forwarder listening on %s:%s",
        GATEWAY_HOST,
        ",".join(str(port) for port in FORWARDED_PORTS),
    )
    for thread in threads:
        thread.join()


if __name__ == "__main__":
    main()
