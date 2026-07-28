"""GIW restart-shaped Next-pickup detection for env-half admission."""

from __future__ import annotations

import re

_GIW = re.escape("git_integration_worker")
_SYNC_RESTART_GIW_RE = re.compile(
    rf"sync_restart[^;\n]*{_GIW}|{_GIW}[^;\n]*sync_restart",
    re.IGNORECASE,
)
_MANAGE_GIW_RESTART_RE = re.compile(
    rf"manage\s*\([^)]*(?:restart|sync_restart|stop)[^)]*{_GIW}|"
    rf"manage\s*\([^)]*{_GIW}[^)]*(?:restart|sync_restart|stop)",
    re.IGNORECASE,
)
_WAIT_HEALTHY_GIW_RESTART_RE = re.compile(
    rf"wait_healthy[^;\n]*(?:sync_restart|restart)[^;\n]*{_GIW}|"
    rf"(?:sync_restart|restart)[^;\n]*wait_healthy[^;\n]*{_GIW}|"
    rf"wait_healthy[^;\n]*{_GIW}[^;\n]*(?:sync_restart|restart)",
    re.IGNORECASE,
)
_BARE_GIW_RESTART_RE = re.compile(
    rf"\brestart\b[^;\n]*{_GIW}|{_GIW}[^;\n]*\brestart\b",
    re.IGNORECASE,
)
_PROBE_ONLY_PICKUP_RE = re.compile(
    r"\b(?:live\s+)?probe\b(?:\s+only|\s+after\s+(?:healthy|restart))",
    re.IGNORECASE,
)


def next_pickup_is_restart_from_holder(item: str) -> bool:
    """True when a Next-pickup row would re-hold GIW for manage restart/drain."""
    text = item.strip()
    if not text:
        return False
    restart_shaped = any(
        pattern.search(text)
        for pattern in (
            _SYNC_RESTART_GIW_RE,
            _MANAGE_GIW_RESTART_RE,
            _WAIT_HEALTHY_GIW_RESTART_RE,
            _BARE_GIW_RESTART_RE,
        )
    )
    if not restart_shaped:
        return False
    if _PROBE_ONLY_PICKUP_RE.search(text) and not (
        _SYNC_RESTART_GIW_RE.search(text) or _MANAGE_GIW_RESTART_RE.search(text)
    ):
        return False
    return True


__all__ = ["next_pickup_is_restart_from_holder"]
