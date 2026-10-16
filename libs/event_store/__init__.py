"""event_store - embeddable event service library.

Start with start_event_service() or run standalone: python -m event_store serve
"""

from .ingest import IngestServer
from .store import EventStore, register_write_fail_hook

# Server imports uvicorn. Load it only when a caller asks for the server API so
# query_client (manage grokbot watch, topology) can import without that dependency.
_SERVER_EXPORTS = ("create_app", "run_service", "start_event_service")


def __getattr__(name: str):
    if name in _SERVER_EXPORTS:
        from . import server

        return getattr(server, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# Harvest nominates these manage slugs when this lib lands (package-grain).
CONSUMERS: tuple[str, ...] = ("cloud_proxy", "git_integration_worker", "stargate")

__all__ = [
    "start_event_service",
    "run_service",
    "create_app",
    "EventStore",
    "IngestServer",
    "register_write_fail_hook",
]
