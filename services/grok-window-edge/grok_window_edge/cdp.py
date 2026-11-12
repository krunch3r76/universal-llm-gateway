"""CDP target validation. This module opens no connection.

A1 ships one handler, ``ping``, which touches no browser, so nothing here
dials anything: these are the guards that a later phase's handlers must pass
through, proven now by their own tests rather than after a window is already
being driven.

Deny-by-default: a port outside ``CDP_PORT_ALLOWLIST`` is refused, and the
denied display and profile directory are refused even if someone widens that
set.
"""

from __future__ import annotations

import os

from .config import CDP_PORT_ALLOWLIST, DENIED_DISPLAYS, DENIED_PROFILE_DIRS


class CdpPortRefusedError(Exception):
    """A CDP target this process is not allowed to reach."""


def cdp_endpoint(port: int) -> str:
    """Return the loopback CDP endpoint for *port*.

    Building the URL is the whole operation; no socket is opened here.

    Raises:
        CdpPortRefusedError: when *port* is not in the explicit allowlist.
    """
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
