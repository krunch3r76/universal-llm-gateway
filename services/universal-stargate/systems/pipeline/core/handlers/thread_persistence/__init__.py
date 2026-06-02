"""thread_persistence — anchor, window, and turn-artifact helpers.

Phase 3 substrate for the cortex-chat-openai compactor. Owns the
asynchronous cortex-api UDS client (``cx_async``), the thread-anchor
resolver, the referential-window builder, and the per-turn JSON artifact
writer. Phase 4 handlers (``assemble_thread``, ``archive_user_turn``,
``archive_assistant_turn``) compose these primitives.
"""

from __future__ import annotations

from .anchor import resolve_or_create_anchor
from .archive_text import (
    is_tool_synthesized_archive_text,
    synthesize_assistant_archive_text,
)
from .artifact import turn_artifact_uri, write_turn_artifact
from .binding import ThreadBinding, require_thread_binding
from .events import cx_async
from .observability import publish_compaction_event
from .window import build_referential_window

__all__ = [
    "ThreadBinding",
    "build_referential_window",
    "require_thread_binding",
    "cx_async",
    "is_tool_synthesized_archive_text",
    "publish_compaction_event",
    "resolve_or_create_anchor",
    "synthesize_assistant_archive_text",
    "turn_artifact_uri",
    "write_turn_artifact",
]
