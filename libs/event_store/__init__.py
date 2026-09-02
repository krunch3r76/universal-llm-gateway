"""event_store - embeddable event service library.

Start with start_event_service() or run standalone: python -m event_store serve
"""

from .ingest import IngestServer
from .server import create_app, run_service, start_event_service
from .store import EventStore, register_write_fail_hook

# Harvest nominates these manage slugs when this lib lands (package-grain).
CONSUMERS: tuple[str, ...] = ('cloud_proxy', 'git_integration_worker', 'stargate')

__all__ = [
    "start_event_service",
    "run_service",
    "create_app",
    "EventStore",
    "IngestServer",
    "register_write_fail_hook",
]
