"""Package entrypoint for the RAG FastAPI service."""

from .main import app, get_event_bus

__all__ = ["app", "get_event_bus"]
