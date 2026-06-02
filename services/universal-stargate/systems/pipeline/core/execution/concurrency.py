"""Pipeline-level concurrency gate surface.

Phase A closure of cortex-chat-openai. Provides ``maybe_concurrency_gate``
— an async context manager that serialises pipeline executions on a
resolved string key when the pipeline spec declares a ``concurrency:``
block at top level of the pipeline YAML.

YAML surface (stable across the FifoCapacityGate swap)::

    concurrency:
      key: "chat:{context.chat_id}"
      timeout_seconds: 30   # optional; default 30s

Resolution rules:

- ``{context.chat_id}`` is the only documented placeholder. Any other
  ``{...}`` token raises ``ValueError`` at run time.
- ``chat_id`` absent when the key references ``{context.chat_id}``
  raises ``ValueError`` — matches ``assemble_thread_v1``'s
  raise-on-missing-chat-id discipline (handlers/assemble_thread.py).
- Serialisation is delegated to a :class:`ConcurrencyBackend` instance
  (typically ``PipelineExecutor._concurrency_backend``). The
  in-process backend wraps :class:`FifoCapacityGate` at ``limit=1`` per
  resolved key with TTL eviction on release-when-idle.
- The error class name :class:`ConcurrencyLockTimeoutError` is
  retained for API-surface stability — it is part of the structured
  error contract caught by ``_normalize_pipeline_exception``. The
  class name preserves the legacy "Lock" terminology; the surface
  itself is a FIFO gate.

Invariants:

- ∀ resolved_key: at most one execution holds the gate slot at any moment.
- Release is structural (``async with``) — guaranteed on every exit
  path (return, raise, cancel).
- Pipelines without a ``concurrency:`` block bypass the surface
  entirely; ``yield`` runs without any gate work.
"""

from __future__ import annotations

import re
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from .errors import ConcurrencyLockTimeoutError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from ..handlers import PipelineContext
    from ..schemas import PipelineSpec
    from .concurrency_backend import ConcurrencyBackend


_PLACEHOLDER_RE = re.compile(r"\{([^{}]+)\}")
_SUPPORTED_PLACEHOLDERS = frozenset({"context.chat_id", "context.dispatch_thread_id"})
_DEFAULT_TIMEOUT_SECONDS = 30.0


@asynccontextmanager
async def maybe_concurrency_gate(
    pipeline: PipelineSpec,
    pipeline_context: PipelineContext,
    backend: ConcurrencyBackend,
) -> AsyncIterator[None]:
    """Acquire a per-key serialisation slot via ``backend``, if declared.

    No-op when the pipeline spec carries no ``concurrency:`` block.
    Gate acquisition is bounded by ``timeout_seconds``; exhausting the
    timeout raises :class:`ConcurrencyLockTimeoutError`. The slot is
    held for the lifetime of the ``async with`` body and released on
    every exit path (return, raise, cancel).
    """
    resolved_key, timeout_seconds = _resolve_lock_spec(pipeline, pipeline_context)
    if resolved_key is None:
        yield
        return

    try:
        await backend.acquire(
            resolved_key,
            timeout=timeout_seconds,
            request_id=pipeline_context.execution_id,
        )
    except TimeoutError as exc:
        raise ConcurrencyLockTimeoutError(
            pipeline_id=pipeline.id,
            execution_id=pipeline_context.execution_id,
            key=resolved_key,
            timeout_seconds=timeout_seconds,
        ) from exc
    try:
        yield
    finally:
        await backend.release(resolved_key)


def _resolve_lock_spec(
    pipeline: PipelineSpec,
    pipeline_context: PipelineContext,
) -> tuple[str | None, float]:
    """Resolve ``concurrency:`` to ``(key, timeout_seconds)`` or ``(None, _)``.

    Returns ``(None, ...)`` when the pipeline has no ``concurrency:``
    block (or the block is malformed in a way that disables the
    feature — non-dict, missing key, empty-string key). Otherwise
    resolves the key template against ``pipeline_context`` and returns
    the concrete key string plus the parsed timeout.

    Raises ``ValueError`` when:

    - ``timeout_seconds`` is non-numeric.
    - The key template references an unknown placeholder.
    - A referenced context attribute is unset (matches
      ``assemble_thread_v1``'s missing-chat-id discipline).
    """
    extras = pipeline.model_extra or {}
    concurrency = extras.get("concurrency")
    if not isinstance(concurrency, dict):
        return None, _DEFAULT_TIMEOUT_SECONDS

    key_template = concurrency.get("key")
    if not isinstance(key_template, str) or not key_template:
        return None, _DEFAULT_TIMEOUT_SECONDS

    timeout_raw = concurrency.get("timeout_seconds", _DEFAULT_TIMEOUT_SECONDS)
    try:
        timeout_seconds = float(timeout_raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"Pipeline {pipeline.id!r}: concurrency.timeout_seconds must be "
            f"numeric, got {timeout_raw!r}"
        ) from exc

    resolved = _render_key(pipeline, pipeline_context, key_template)
    return resolved, timeout_seconds


def _render_key(
    pipeline: PipelineSpec,
    pipeline_context: PipelineContext,
    key_template: str,
) -> str:
    """Replace ``{context.<attr>}`` placeholders with concrete values.

    Phase A supports only ``{context.chat_id}``. Unknown placeholder
    names raise ``ValueError`` (closes the substring-permissive
    ``.replace()`` gap). A referenced attribute that resolves to
    ``None`` or empty also raises — matching the runtime
    raise-on-missing-chat-id discipline of ``assemble_thread_v1``.
    """

    def _substitute(match: re.Match[str]) -> str:
        token = match.group(1)
        if token not in _SUPPORTED_PLACEHOLDERS:
            raise ValueError(
                f"Pipeline {pipeline.id!r}: concurrency.key references "
                f"unsupported placeholder {{{token}}}. Supported: "
                f"{sorted(_SUPPORTED_PLACEHOLDERS)}"
            )
        if token == "context.chat_id":
            chat_id = pipeline_context.chat_id
            if not chat_id:
                raise ValueError(
                    f"Pipeline {pipeline.id!r}: concurrency.key references "
                    f"{{context.chat_id}} but request did not provide chat_id"
                )
            return chat_id
        dispatch_thread_id = getattr(pipeline_context, "dispatch_thread_id", None)
        if not dispatch_thread_id:
            raise ValueError(
                f"Pipeline {pipeline.id!r}: concurrency.key references "
                f"{{context.dispatch_thread_id}} but request did not provide "
                f"dispatch_thread_id"
            )
        return dispatch_thread_id

    return _PLACEHOLDER_RE.sub(_substitute, key_template)
