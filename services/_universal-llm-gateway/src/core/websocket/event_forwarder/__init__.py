"""Forward EventBus events to WebSocket clients — package-shadow of event_forwarder.py.

Re-exports WebSocketEventForwarder so existing imports from
src.core.websocket.event_forwarder and src.core.websocket keep working.
"""

from .forwarder import WebSocketEventForwarder

__all__ = ["WebSocketEventForwarder"]
