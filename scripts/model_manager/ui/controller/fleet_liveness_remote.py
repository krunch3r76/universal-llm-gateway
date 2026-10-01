"""Remote health URL helpers for fleet liveness load markers."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from ..model.service_state import ServiceInfo
from .fleet_liveness_probe import HOST_CLOCK_GRANULARITY_S


def _health_url_is_remote(health_url: str | None) -> bool:
    """True when ``health_url`` names a host this machine's ``/proc`` cannot see."""
    if not health_url or health_url.startswith("unix:"):
        return False
    parsed = urlparse(health_url)
    if parsed.scheme not in {"http", "https"}:
        return False
    host = parsed.hostname
    if not host:
        return False
    from .service_config import is_cdp_ask_local_host

    return not is_cdp_ask_local_host(host)


def _remote_pid_unmeasured(info: ServiceInfo) -> dict[str, Any]:
    """State that a remote-reported pid was not looked up in local ``/proc``.

    ``value_utc`` stays null. The error is that sentence, not a liveness
    answer and not an ``off_host`` flag.
    """
    return {
        "kind": "host_proc_start",
        "value_utc": None,
        "granularity_s": HOST_CLOCK_GRANULARITY_S,
        "clock_domain": "host_proc",
        "error": (
            f"pid {info.pid} reported by {info.health_url} is not on this host; "
            "local /proc was not read"
        ),
    }
