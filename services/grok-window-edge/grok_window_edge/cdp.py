"""CDP target validation. This module opens no connection.

A1 ships one handler, ``ping``, which touches no browser, so nothing here
dials anything: these are the guards that a later phase's handlers must pass
through, proven now by their own tests rather than after a window is already
being driven.

Deny-by-default: a port outside ``CDP_PORT_ALLOWLIST`` is refused, and the
denied ports, display and profile directory are refused even if someone widens
that set.
"""

from __future__ import annotations

import os
from urllib.parse import urlparse

from .config import (
    CDP_PORT_ALLOWLIST,
    DENIED_DISPLAYS,
    DENIED_PORTS,
    DENIED_PROFILE_DIRS,
)


class CdpPortRefusedError(Exception):
    """A CDP target this process is not allowed to reach."""


def assert_cdp_origin_allowed(origin: str) -> None:
    """Refuse a CDP origin whose port this process may not dial.

    Raises:
        CdpPortRefusedError: when the URL port is missing, hard-denied, or not
            allowlisted.
    """
    from cursor_bridge.cdp_pages import (
        CdpWebSocketRefusedError,
        assert_loopback_cdp_http_origin,
    )

    try:
        assert_loopback_cdp_http_origin(origin)
    except CdpWebSocketRefusedError as exc:
        raise CdpPortRefusedError(str(exc)) from exc
    parsed = urlparse(origin.strip())
    port = parsed.port
    if port is None:
        raise CdpPortRefusedError(
            f"CDP origin {origin!r} has no port for grok-window-edge"
        )
    cdp_endpoint(port)


def cdp_endpoint(port: int) -> str:
    """Return the loopback CDP endpoint for *port*.

    Building the URL is the whole operation; no socket is opened here.

    The hard deny is checked before the allowlist is consulted, so a port on
    ``DENIED_PORTS`` stays refused even once someone adds it to the allowlist.
    That ordering is the whole reason the two lists are separate.

    Raises:
        CdpPortRefusedError: when *port* is hard-denied, or is not in the
            explicit allowlist.
    """
    if port in DENIED_PORTS:
        raise CdpPortRefusedError(
            f"CDP port {port} is hard-denied for grok-window-edge"
        )
    if port not in CDP_PORT_ALLOWLIST:
        allowed = ", ".join(
            str(allowed_port) for allowed_port in sorted(CDP_PORT_ALLOWLIST)
        )
        raise CdpPortRefusedError(
            f"CDP port {port} is not allowlisted for grok-window-edge "
            f"(allowed: {allowed})"
        )
    return f"http://127.0.0.1:{port}"


def assert_display_allowed(display: str) -> None:
    """Refuse a display this process must never touch.

    Raises:
        CdpPortRefusedError: when *display* is hard-denied (:11).
    """
    if display.strip() in DENIED_DISPLAYS:
        raise CdpPortRefusedError(
            f"display {display} is hard-denied for grok-window-edge"
        )


def assert_profile_dir_allowed(path: str) -> None:
    """Refuse a browser profile directory this process must never touch.

    Raises:
        CdpPortRefusedError: when *path* resolves under a hard-denied profile.
    """
    candidate = os.path.normpath(os.path.expanduser(path.strip()))
    for denied in DENIED_PROFILE_DIRS:
        denied_path = os.path.normpath(os.path.expanduser(denied))
        if candidate == denied_path or candidate.startswith(f"{denied_path}{os.sep}"):
            raise CdpPortRefusedError(
                f"profile dir {path} is hard-denied for grok-window-edge"
            )
