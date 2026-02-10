"""
Monitoring infrastructure for universal_event_bus.

Provides message schemas and utilities for event monitoring based on UML Messages.
"""

from .message_schemas import MonitoringMessage, serialize_event, serialize_event_to_json

__all__ = ["MonitoringMessage", "serialize_event", "serialize_event_to_json"]
