"""Pipeline-level concurrency lock surface.

Phase 5 of cortex-chat-openai (Phase A — thin in-process backing).
Provides ``maybe_concurrency_lock`` — an async context manager that
serialises pipeline executions on a resolved string key when the
pipeline spec declares a ``concurrency:`` block at top level of the
pipeline YAML.

YAML surface::

    concurrency:
      key: "chat:{context.chat_id}"
      timeout_seconds: 30   # optional; default 30s

Resolution rules:

- ``{context.chat_id}`` is the only documented placeholder in Phase A.
  Any other ``{...}`` token raises ``ValueError`` at run time.
- ``chat_id`` absent when the key references ``{context.chat_id}``
  raises ``ValueError`` — matches ``assemble_thread_v1``'s
  raise-on-missing-chat-id discipline (handlers/assemble_thread.py).
- Lock store is a plain ``dict[str, asyncio.Lock]`` injected by the
  caller (typically ``PipelineExecutor._concurrency_locks``). Leaks
  one ``Lock`` per distinct resolved key for the process lifetime.
  Acceptable at Phase A scale (~150 B per Lock × N chat_ids per
  master Stargate restart cadence). Substrate-level cleanup promotes
  to Phase B without YAML or pipeline-author impact.
- ``asyncio.timeout()`` context manager (3.11+) is used; stargate
  ``pyproject.toml`` pins ``requires-python = ">=3.12"`` so the safer
  pattern is available and the ``asyncio.wait_for(lock.acquire(), …)``
  cancellation race does not apply.

Invariants:

- ∀ resolved_key: at most one execution holds the lock at any moment.
- Lock release is structural (``async with``) — guaranteed on every
  exit path (return, raise, cancel).
- Pipelines without a ``concurrency:`` block bypass the surface
  entirely; ``yield`` runs without any locking work.
"""

from __future__ import annotations

import asyncio
import re
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from .errors import ConcurrencyLockTimeoutError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from ..handlers import PipelineContext
    from ..schemas import PipelineSpec


_PLACEHOLDER_RE = re.compile(r"\{([^{}]+)\}")
_SUPPORTED_PLACEHOLDERS = frozenset({"context.chat_id"})
_DEFAULT_TIMEOUT_SECONDS = 30.0


@asynccontextmanager
async def maybe_concurrency_lock(
    pipeline: PipelineSpec,
    pipeline_context: PipelineContext,
    locks: dict[str, asyncio.Lock],
) -> AsyncIterator[None]:
    """Acquire a per-key serialisation lock for the pipeline, if declared.

    No-op when the pipeline spec carries no ``concurrency:`` block.
    Lock acquisition is bounded by ``timeout_seconds``; exhausting the
    timeout raises ``ConcurrencyLockTimeoutError``. The lock is held
    for the lifetime of the ``async with`` body and released on every
    exit path (return, raise, cancel).
    """
    resolved_key, timeout_seconds = _resolve_lock_spec(pipeline, pipeline_context)
    if resolved_key is None:
        yield
        return

    lock = locks.setdefault(resolved_key, asyncio.Lock())
    try:
        async with asyncio.timeout(timeout_seconds):
            await lock.acquire()
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
        lock.release()


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
    names raise ``ValueError`` (matches the substring-permissive
    ``.replace()`` gap called out in the Phase 5 kickoff prompt).
    A referenced attribute that resolves to ``None`` or empty also
    raises — matching the runtime raise-on-missing-chat-id discipline
    of ``assemble_thread_v1``.
    """

    def _substitute(match: re.Match[str]) -> str:
        token = match.group(1)
        if token not in _SUPPORTED_PLACEHOLDERS:
            raise ValueError(
                f"Pipeline {pipeline.id!r}: concurrency.key references "
                f"unsupported placeholder {{{token}}}. Supported: "
                f"{sorted(_SUPPORTED_PLACEHOLDERS)}"
            )
        # Only "context.chat_id" is supported in Phase A.
        chat_id = pipeline_context.chat_id
        if not chat_id:
            raise ValueError(
                f"Pipeline {pipeline.id!r}: concurrency.key references "
                f"{{context.chat_id}} but request did not provide chat_id"
            )
        return chat_id

    return _PLACEHOLDER_RE.sub(_substitute, key_template)
