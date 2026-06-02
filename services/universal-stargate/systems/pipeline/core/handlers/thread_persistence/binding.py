"""Resolve cortex thread persistence binding from pipeline context.

OpenAI chat compaction uses ``thread:openai-chat:{chat_id}``.
Team dispatch compaction (Phase D) uses ``thread:dispatch:{dispatch_thread_id}``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..protocol import PipelineContext


@dataclass(frozen=True, slots=True)
class ThreadBinding:
    """Anchor namespace + storage key for assemble/archive handlers."""

    kind: str
    key: str

    @property
    def anchor_id(self) -> str:
        return f"thread:{self.kind}:{self.key}"

    @property
    def storage_key(self) -> str:
        """Artifact directory key (``thread-artifacts/{storage_key}/``)."""
        return self.key


def require_thread_binding(context: PipelineContext) -> ThreadBinding:
    """Return the active thread binding or raise with step-oriented guidance."""
    dispatch_thread_id = getattr(context, "dispatch_thread_id", None)
    if isinstance(dispatch_thread_id, str) and dispatch_thread_id.strip():
        return ThreadBinding(kind="dispatch", key=dispatch_thread_id.strip())
    chat_id = context.chat_id
    if isinstance(chat_id, str) and chat_id.strip():
        return ThreadBinding(kind="openai-chat", key=chat_id.strip())
    raise ValueError(
        "thread persistence requires context.dispatch_thread_id or context.chat_id"
    )
