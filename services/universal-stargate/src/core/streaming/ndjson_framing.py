"""NDJSON bytes line framer for httpx streaming responses.

Converts arbitrary byte chunks into complete NDJSON lines without
intermediate UTF-8 decode/encode. Preserves framing invariant:
∀ yielded: exactly one JSON object terminated by b"\\n".
"""

from collections.abc import AsyncIterator

import httpx


async def iter_ndjson_lines_bytes(response: httpx.Response) -> AsyncIterator[bytes]:
    """Yield complete NDJSON lines as bytes from an httpx streaming response.

    Buffers partial lines across chunk boundaries. Splits on b"\\n".
    Skips blank/whitespace-only lines. Flushes residual buffer at end.

    INVARIANT: ∀ yielded line: line == json_object_bytes + b"\\n"

    Args:
        response: An httpx.Response being consumed via .aiter_bytes()

    Yields:
        Complete NDJSON line as bytes (including trailing newline)
    """
    buf = b""
    async for chunk in response.aiter_bytes():
        buf += chunk
        # Split completed lines
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            if line.strip():
                yield line + b"\n"

    # Flush residual (partial line without trailing newline)
    if buf.strip():
        yield buf.strip() + b"\n"
