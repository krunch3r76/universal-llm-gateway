"""thread_persistence — anchor, window, and turn-artifact helpers.

Phase 3 substrate for the cortex-chat-openai compactor. Owns the
asynchronous cortex-api UDS client (``cx_async``), the thread-anchor
resolver, the referential-window builder, and the per-turn JSON artifact
writer. Phase 4 handlers (``assemble_thread``, ``archive_user_turn``,
``archive_assistant_turn``) compose these primitives.
"""

from __future__ import annotations

from .anchor import resolve_or_create_anchor
from .artifact import turn_artifact_uri, write_turn_artifact
from .events import cx_async
from .window import build_referential_window

__all__ = [
    "build_referential_window",
    "cx_async",
    "resolve_or_create_anchor",
    "turn_artifact_uri",
    "write_turn_artifact",
]
