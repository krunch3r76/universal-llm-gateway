"""Internal emit helper shared by grokbuild event sub-modules.

``mcp_events`` is only available in the mcp-server process context. In the
worker process (and in pytest without the mcp-server on sys.path) the
import fails and ``record`` falls back to an injectable UDS publisher
hook — when the worker installs its publisher via :func:`register_uds_publisher`
at lifespan startup, all lib-level events (``mcp.grokbuild.dispatch.*``,
``mcp.grokbuild.create.*``, ``mcp.grokbuild.remove.*``,
``mcp.grokbuild.list.*``, ``mcp.grokbuild.registry.recovered``) flow through
the worker's UDS path to the event service. Without that hook the fallback
remains a no-op (e.g. pytest contexts that don't exercise the worker app).

This dependency direction is one-way: the lib never imports from
``services.grokbuild_worker``. The worker's app lifespan injects the
publisher; the lib only knows about the hook signature.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from universal_event_bus import Event

_uds_publisher: Callable[[str, dict[str, Any]], None] | None = None


def register_uds_publisher(publisher: Callable[[str, dict[str, Any]], None]) -> None:
    """Install a UDS publisher for lib events (worker-context use only).

    The mcp-server context resolves the real ``mcp_events.record`` at
    import time and never reaches this hook. The worker resolves the
    ImportError fallback (below) and its lifespan startup calls this
    function with the worker's ``_emit_uds`` so audit-rich lib events
    (read_only_violation, git_status_pre/post, sidecar_gaps, …) reach
    the event service rather than being silently dropped.
    """
    global _uds_publisher
    _uds_publisher = publisher


try:
    from mcp_events import record
except ImportError:

    def record(signal: str, **payload: Any) -> None:  # type: ignore[misc]
        """Worker-context fallback: route through ``_uds_publisher`` when installed.

        When no publisher is installed (e.g. pytest contexts that don't
        exercise the worker app), the call is a no-op — preserving the
        prior test contract that lib events do not touch any I/O surface
        outside ``event_log`` fixtures.
        """
        if _uds_publisher is None:
            return
        _uds_publisher(signal, dict(payload))


def _emit(event: Event) -> None:
    record(event.signal, **event.payload)
