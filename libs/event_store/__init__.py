"""event_store - embeddable event service library.

Start with start_event_service() or run standalone: python -m event_store serve
"""

from .ingest import IngestServer
from .server import create_app, run_service, start_event_service
from .store import EventStore

__all__ = [
    "start_event_service",
    "run_service",
    "create_app",
    "EventStore",
    "IngestServer",
]
