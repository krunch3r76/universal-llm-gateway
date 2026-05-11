"""Native provider streaming helpers (cloud proxy)."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx


async def _empty_byte_stream() -> AsyncIterator[bytes]:
    if False:  # pragma: no cover — async generator must yield on some path
        yield b""


async def preflight_native_byte_stream(
    source: AsyncIterator[bytes],
) -> AsyncIterator[bytes]:
    """Pull the first chunk from upstream before committing to chunked streaming.

    If the provider rejects the request before any bytes are emitted (typical
    for validation failures surfaced as ``httpx.HTTPStatusError`` inside the
    adapter's async generator), that exception is raised here so FastAPI can
    return a structured error instead of starting a ``StreamingResponse`` that
    later fails as an incomplete chunked read.
    """
    try:
        first = await anext(source)
    except StopAsyncIteration:
        return _empty_byte_stream()
    except httpx.HTTPStatusError:
        await source.aclose()
        raise
    except BaseException:
        await source.aclose()
        raise

    async def _rest() -> AsyncIterator[bytes]:
        try:
            yield first
            async for chunk in source:
                yield chunk
        finally:
            await source.aclose()

    return _rest()
