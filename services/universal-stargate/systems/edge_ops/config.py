"""Gate flag, bounds and edge-token wiring for the edge-op substrate.

Environment keys. No secret value is logged, echoed in a response, or returned
by any function here other than the comparison the auth dependency performs.

``EDGE_OPS_ENABLED``
    Master gate, default ``false``. Parsed per request rather than at import,
    so the flag needs no mount-time decision: every ``/api/v1/edge/*`` route
    refuses with ``edge_ops_disabled`` while it is off.

``EDGE_GLASS_WAKE_RENAME_ENABLED`` (MCP only, default ``false``)
    When ``true`` **and** ``EDGE_OPS_ENABLED`` is on, MCP ``cursor_bridge``
    ``wake_tab`` / ``rename_tab`` admit through Stargate instead of dialing CDP
    locally. Both must be on; either off keeps the legacy path unchanged.

``EDGE_OPS_CURSOR_GLASS`` (Stargate admit gate, default ``false``)
    When off, ``kind=cursor_glass`` admits are refused with ``edge_kind_disabled``
    even if ``EDGE_OPS_ENABLED`` is on.
``EDGE_OPS_EDGE_TOKENS``
    Per-edge bearer map, ``<edge_id>:<token>[,<edge_id>:<token>...]``. The
    lease, event, result, release and directive routes accept only the token
    bound to the ``edge_id`` in the request. The edge holds no MCP token.
``DATA_DIR``
    Journal home, default ``~/.gateway`` — the convention the pipeline dispatch
    journal already uses. The edge-op authority store is
    ``$DATA_DIR/edge-ops.db``.

The bounds below are the design's, not tuning knobs: 60 s is the long-poll
hold from the dispatch design's resource table, 30 s is the caller-facing
await ceiling from plan binding 3, and two long-poll intervals is the
edge-presence window that same binding fixes for ``edge_unavailable``.
"""

from __future__ import annotations

import os
from pathlib import Path

ENABLED_ENV_KEY = "EDGE_OPS_ENABLED"
CURSOR_GLASS_ENV_KEY = "EDGE_OPS_CURSOR_GLASS"
EDGE_TOKENS_ENV_KEY = "EDGE_OPS_EDGE_TOKENS"
DATA_DIR_ENV_KEY = "DATA_DIR"

JOURNAL_FILENAME = "edge-ops.db"

LEASE_TTL_SECONDS = 30.0
LONG_POLL_SECONDS = 60.0
AWAIT_MAX_SECONDS = 30.0
EDGE_PRESENCE_INTERVALS = 2
DEFAULT_DEADLINE_SECONDS = 120.0
MAX_DEADLINE_SECONDS = 900.0

# The dedupe window must cover at least the op deadline so a caller retrying
# its own admit inside the op's lifetime gets the same op id back. The grace
# keeps a retry that races the deadline on the same answer.
DEDUPE_GRACE_SECONDS = 60.0

_TRUE_VALUES = frozenset({"true", "1", "yes", "on"})


def edge_ops_enabled() -> bool:
    """Read the gate flag now. Absent or unparseable means off."""
    return os.getenv(ENABLED_ENV_KEY, "").strip().lower() in _TRUE_VALUES


def cursor_glass_admit_enabled() -> bool:
    """Whether ``kind=cursor_glass`` may be admitted (default off)."""
    return os.getenv(CURSOR_GLASS_ENV_KEY, "").strip().lower() in _TRUE_VALUES


def data_dir() -> Path:
    """Journal home, honouring ``DATA_DIR`` with the ``~/.gateway`` default."""
    return Path(os.getenv(DATA_DIR_ENV_KEY, str(Path.home() / ".gateway"))).expanduser()


def journal_path() -> Path:
    """Absolute path of the edge-op authority store."""
    return data_dir() / JOURNAL_FILENAME


def edge_presence_window_seconds() -> float:
    """How stale an edge's last poll may be before ``admit`` fails fast."""
    return LONG_POLL_SECONDS * EDGE_PRESENCE_INTERVALS


def edge_token_map() -> dict[str, str]:
    """Parse ``EDGE_OPS_EDGE_TOKENS`` into ``{edge_id: token}``.

    Malformed entries are dropped silently rather than logged, because the
    entry text contains the token. An empty map means no edge can lease.
    """
    raw = os.getenv(EDGE_TOKENS_ENV_KEY, "")
    tokens: dict[str, str] = {}
    for entry in raw.split(","):
        edge_id, separator, token = entry.strip().partition(":")
        if not separator:
            continue
        edge_id = edge_id.strip()
        token = token.strip()
        if edge_id and token:
            tokens[edge_id] = token
    return tokens


def clamp_deadline_seconds(deadline_s: float | None) -> float:
    """Clamp a caller's deadline budget into the substrate's bounds."""
    if deadline_s is None:
        return DEFAULT_DEADLINE_SECONDS
    return max(1.0, min(float(deadline_s), MAX_DEADLINE_SECONDS))


def clamp_await_seconds(timeout_s: float | None) -> float:
    """Clamp a caller's await budget to the binding-3 ceiling of 30 s."""
    if timeout_s is None:
        return AWAIT_MAX_SECONDS
    return max(0.0, min(float(timeout_s), AWAIT_MAX_SECONDS))


def clamp_long_poll_seconds(wait_s: float | None) -> float:
    """Clamp an edge's long-poll hold to at most one 60 s interval."""
    if wait_s is None:
        return LONG_POLL_SECONDS
    return max(0.0, min(float(wait_s), LONG_POLL_SECONDS))
