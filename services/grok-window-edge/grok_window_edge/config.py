"""Edge configuration: the CDP port allowlist, hard denies, and identity keys.

The allowlist is an explicit set, never a range, and it is deny-by-default:
A1 serves the grok-window CDP ports from ``config/grok-windows.json`` and
nothing else. Cursor's :8 port is added when the cursor kinds go live in a
later phase, by editing this set — not by widening a bound.

Hard denies are separate from "not allowlisted" because they must survive
someone adding a port later: CDP ports 9341 and 9314, display :11 and the
``~/.gateway/chrome-playground`` profile belong to the cookie-sustain owner,
and this process must never touch them. ``DENIED_PORTS`` is therefore checked
before ``CDP_PORT_ALLOWLIST`` is consulted at all, not merely left out of it.

Environment keys:

``GROK_WINDOW_EDGE_ID``
    This edge's identity, sent as ``X-Edge-Id`` and bound to its bearer token
    in Stargate's ``EDGE_OPS_EDGE_TOKENS``.
``GROK_WINDOW_EDGE_TOKEN``
    The bearer this edge presents to Stargate. The edge holds no MCP token
    and no database DSN.
``ULG_DISPLAY_LOCK_DIR``
    The display-interaction lock directory. A1 reads the value only, to report
    it; this process acquires no lock. Making it explicit here is the
    binding-4 requirement that every lock-taker name the same directory before
    any lock-taking op is flipped on. When locks are eventually taken they are
    taken on configured grok displays (and :8 when cursor is live), never on :11.
``STARGATE_URL`` / ``STARGATE_UNIX_SOCKET`` / ``STARGATE_PORT``
    Resolved by ``transport_utils``; the edge's only outbound HTTP is to
    Stargate.
"""

from __future__ import annotations

import os

# Explicit set from grok-windows.json (never a numeric range). Cursor :8 (9338)
# is added when the cursor kinds go live — not in this slice.
def _grok_cdp_port_allowlist() -> frozenset[int]:
    from cursor_bridge.grok_window_bindings import configured_grok_cdp_ports

    return configured_grok_cdp_ports()


CDP_PORT_ALLOWLIST: frozenset[int] = _grok_cdp_port_allowlist()

# Never reachable from this process, whatever else is added above. The port
# denies are checked before the allowlist, so releasing 9341 into it later —
# which binding 8's flips-if explicitly contemplates — still cannot reach the
# cookie-sustain window; 9314 is not grok display :14 (grok :14 is 9344).
DENIED_PORTS: frozenset[int] = frozenset({9314, 9341})
DENIED_DISPLAYS: frozenset[str] = frozenset({":11"})
DENIED_PROFILE_DIRS: tuple[str, ...] = ("~/.gateway/chrome-playground",)

EDGE_ID_ENV_KEY = "GROK_WINDOW_EDGE_ID"
EDGE_TOKEN_ENV_KEY = "GROK_WINDOW_EDGE_TOKEN"
DISPLAY_LOCK_DIR_ENV_KEY = "ULG_DISPLAY_LOCK_DIR"

DEFAULT_EDGE_ID = "grok-window-edge"
DEFAULT_LEASE_WAIT_SECONDS = 60.0
DEFAULT_DIRECTIVE_POLL_SECONDS = 1.0
DEFAULT_BACKOFF_INITIAL_SECONDS = 1.0
DEFAULT_BACKOFF_MAX_SECONDS = 30.0
DEFAULT_KINDS: tuple[str, ...] = ("ping",)
KINDS_ENV_KEY = "GROK_WINDOW_EDGE_KINDS"


def edge_id() -> str:
    """This edge's identity as Stargate knows it."""
    return os.getenv(EDGE_ID_ENV_KEY, DEFAULT_EDGE_ID).strip() or DEFAULT_EDGE_ID


def edge_token() -> str:
    """The bearer this edge presents. Never logged or echoed."""
    return os.getenv(EDGE_TOKEN_ENV_KEY, "").strip()


def edge_kinds() -> tuple[str, ...]:
    """Kinds this process leases for, from ``GROK_WINDOW_EDGE_KINDS`` (default ping)."""
    raw = (os.getenv(KINDS_ENV_KEY) or "ping").strip()
    kinds = tuple(part.strip() for part in raw.split(",") if part.strip())
    return kinds or DEFAULT_KINDS


def display_lock_dir() -> str | None:
    """The configured lock directory, read-only. ``None`` when unset.

    A1 reports this value and acquires nothing. An unset value is the gap the
    binding-4 check records rather than a default this process invents.
    """
    value = (os.getenv(DISPLAY_LOCK_DIR_ENV_KEY) or "").strip()
    return value or None
