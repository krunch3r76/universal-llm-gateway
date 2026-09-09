"""Continuity messages envelope — shared schema and helpers."""

from .messages import (
    CORE_KEYS,
    TOOL_MARKER_RE,
    ContinuityMessage,
    ContinuityMessagesEnvelope,
    EnvelopeMeta,
    IndexRow,
    Tools,
    canonical_messages_bytes,
    messages_sha256,
    strip_extras,
    strip_tool_markers,
)

__all__ = [
    "CORE_KEYS",
    "TOOL_MARKER_RE",
    "ContinuityMessage",
    "ContinuityMessagesEnvelope",
    "EnvelopeMeta",
    "IndexRow",
    "Tools",
    "canonical_messages_bytes",
    "messages_sha256",
    "strip_extras",
    "strip_tool_markers",
]
