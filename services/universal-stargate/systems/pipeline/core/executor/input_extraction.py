"""Request-input extraction helpers for the pipeline executor.

These functions read the inbound HTTP request context to produce the
inputs the executor needs before DAG construction:

- ``extract_source_text`` — the user's most recent message text, used as
  the canonical pipeline source ``text``.
- ``extract_messages`` — the full conversation history (preferring the
  pre-truncation capture on ``http_request.state``).
- ``extract_chat_id`` — the persistent chat identifier (for pipelines
  like ``cortex-chat-openai`` that key concurrency or persistence on it).
- ``extract_dispatch_thread_id`` — the team-dispatch compaction key for
  the ``team-dispatch`` pipeline's thread persistence (Phase D).

All four are pure functions of the request context; they hold no state.
"""

from __future__ import annotations

from typing import Any

from .prepared import _PipelineRequestContextProtocol


def extract_dispatch_thread_id(
    context: _PipelineRequestContextProtocol,
) -> str | None:
    """Lift ``dispatch_thread_id`` for team-dispatch compaction (Phase D).

    Reads ``context.original_request["dispatch_thread_id"]`` and returns it stripped
    when it is a non-empty string; missing, blank or non-string values yield ``None``.
    Called by ``preparation`` when building the prepared source input.
    """
    if not context.original_request:
        return None
    raw = context.original_request.get("dispatch_thread_id")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None


def extract_chat_id(context: _PipelineRequestContextProtocol) -> str | None:
    """Lift ``chat_id`` from ``context.original_request`` for persistent chat
    pipelines (e.g. ``cortex-chat-openai``).

    Returns the stripped string when present and non-empty; ``None`` otherwise.
    Non-string payloads silently coerce to ``None`` — the pipeline definition
    decides whether absence is a hard error via step-level validation.
    """
    if not context.original_request:
        return None
    raw = context.original_request.get("chat_id")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None


def extract_messages(
    context: _PipelineRequestContextProtocol,
) -> list[dict[str, Any]] | None:
    """Return the full chat history, preferring the explicit pre-truncation capture.

    Checks ``http_request.state.pipeline_full_messages`` first (set before any
    context truncation), then falls back to a non-empty ``messages`` list on
    ``original_request``. Returns ``None`` when neither source is available.
    """
    if hasattr(context.http_request, "state") and hasattr(
        context.http_request.state, "pipeline_full_messages"
    ):
        return context.http_request.state.pipeline_full_messages

    if context.original_request:
        messages = context.original_request.get("messages")
        if messages and isinstance(messages, list):
            return messages
    return None


def extract_source_text(context: _PipelineRequestContextProtocol) -> str:
    """Return the most recent user message text as the canonical pipeline source text.

    Scans ``chat_request.messages`` newest-first for a ``user`` turn, accepting plain
    string content or the first text part of multimodal content; then falls back to
    string content in ``original_request["messages"]``. Returns ``""`` if none found.
    """
    if context.chat_request and context.chat_request.messages:
        for msg in reversed(context.chat_request.messages):
            if msg.role == "user":
                content = msg.content
                if isinstance(content, str):
                    return content
                if isinstance(content, list) and content:
                    for part in content:
                        if isinstance(part, dict) and part.get("type") == "text":
                            return part.get("text", "")
                        if isinstance(part, str):
                            return part

    if context.original_request:
        messages = context.original_request.get("messages", [])
        for msg in reversed(messages):
            if msg.get("role") == "user":
                content = msg.get("content", "")
                if isinstance(content, str):
                    return content

    return ""
