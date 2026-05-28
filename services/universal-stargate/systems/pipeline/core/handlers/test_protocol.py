"""Unit tests for StepOutput's streaming-contract additions.

Covers Phase 3 of ``plan:pipeline-terminal-passthrough-streaming``:

- ``stream`` field defaults to ``None`` on a non-streaming construct.
- ``stream`` field accepts an AsyncIterator and stores it intact.
- Provenance auto-population fires for a streaming StepOutput because
  provenance derives from ``model_id`` alone, independent of streamed
  content; the ``stream`` field itself is untouched by ``__post_init__``.

Companion sidecar:
``cortex:notes/system/threads/pipeline-terminal-passthrough-streaming-arc-phase-3.md``.
"""

from __future__ import annotations

from typing import Any

from systems.pipeline.core.handlers.protocol import StepOutput


class _NoopStream:
    """AsyncIterator that yields nothing — for shape tests only.

    Instantiating an ``async def`` generator function creates a generator
    object that must be either iterated or explicitly closed (otherwise
    Python emits a ``RuntimeWarning`` when GC'd). Using this protocol-
    conforming class instead keeps synchronous tests free of unawaited-
    coroutine warnings while still satisfying the ``AsyncIterator``
    type contract.
    """

    def __aiter__(self) -> _NoopStream:
        return self

    async def __anext__(self) -> dict[str, Any]:
        raise StopAsyncIteration


def test_step_output_stream_default_is_none() -> None:
    """A freshly constructed non-streaming StepOutput has ``stream is None``.

    This is the invariant every existing handler relies on: ``stream`` is
    a new optional field whose default preserves backward compatibility
    with all buffered-path consumers.
    """
    out = StepOutput(raw="hello")
    assert out.stream is None


def test_step_output_with_stream_field() -> None:
    """Constructing a streaming StepOutput stores the iterator intact.

    The dataclass holds the iterator by reference — no copy, no wrap;
    the consumer (Phase 4 lifecycle) drives it via ``async for``.
    """
    stream = _NoopStream()
    out = StepOutput(raw="", stream=stream)
    assert out.stream is stream
    assert out.raw == ""


def test_step_output_provenance_populates_for_streaming() -> None:
    """Streaming StepOutput with ``model_id`` + ``step_id`` still populates provenance.

    Provenance derives from ``model_id`` alone (not from streamed content),
    so the same ``__post_init__`` hook fires for streaming outputs as for
    buffered ones. The ``stream`` field is preserved unchanged by
    ``__post_init__`` — the auto-population logic never reaches it.
    """
    stream = _NoopStream()
    out = StepOutput(
        raw="",
        stream=stream,
        model_id="openai/gpt-5.4",
        step_id="respond",
    )
    assert out.provenance is not None
    # The stream field is preserved unchanged by ``__post_init__``.
    assert out.stream is stream
