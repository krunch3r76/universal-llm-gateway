"""Citeable HTTP log line for one MCP ``tools/call``.

``McpRequestEventsMiddleware`` calls ``ToolsCallIsErrorScan`` while the
response bytes pass through, then ``tools_call_log_line`` once the call
finishes. The line is the excerpt house 16401 can quote: endpoint path,
tool name, ``isError``, ``MCP-Protocol-Version``, ``User-Agent``, and the
already-resolved caller label.

The label is a principal or token id (``cursor``, ``static``, an OAuth
client id). It is never the bearer token. A shared static token stays
``caller=static`` so the missing identity is visible.

This is a plain log, not an event. ``mcp.request.completed`` already fires
on this hot path; a second signal would only repeat it. Gzipped JSON is
inflated in memory one chunk at a time and discarded. ``text/event-stream``
is already uncompressed because GZip skips that content type.
"""

from __future__ import annotations

import re
import zlib
from typing import Any

_IS_ERROR_RE = re.compile(rb'"isError"\s*:\s*(true|false)')
# Long enough to hold ``"isError": false`` when a chunk boundary splits it.
_CARRY_BYTES = 32
_FIELD_LIMIT = 180


def tools_call_log_line(
    *,
    path: str,
    tool_name: str,
    is_error: str,
    protocol: str,
    user_agent: str,
    caller_id: str,
) -> str:
    """Format one ``tools/call`` line a later seat can quote from the server log.

    Blank fields render as ``absent``. Whitespace inside a value is collapsed
    so the record stays a single line. ``caller_id`` must already be a
    principal label; this function does not read or hash an Authorization
    header. No event is emitted and nothing is written except the returned
    string.
    """
    return (
        "MCP tools/call"
        f" path={_log_field(path)}"
        f" tool={_log_field(tool_name)}"
        f" isError={_log_field(is_error)}"
        f" protocol={_log_field(protocol)}"
        f" ua={_log_field(user_agent)}"
        f" caller={_log_field(caller_id)}"
    )


class ToolsCallIsErrorScan:
    """Pull ``result.isError`` from a ``tools/call`` response without keeping it.

    One scan per HTTP request. The middleware passes path, protocol,
    user agent, and caller id up front, then ``arm`` once it knows the
    method is ``tools/call``. ``note_start`` reads response headers,
    ``feed`` takes each body chunk, and ``emit`` writes the single log
    line. Unarmed scans no-op, so list and initialize traffic stay quiet.

    Structural ``"isError"`` keys match; the same word inside a JSON
    string is escaped and does not. The last key wins when a stream
    carries more than one JSON-RPC message. Gzip bodies are inflated
    one chunk at a time and dropped. ``emit`` writes a log line only.
    """

    def __init__(
        self,
        *,
        path: str,
        protocol: str,
        user_agent: str,
        caller_id: str,
    ) -> None:
        self._path = path
        self._protocol = protocol
        self._user_agent = user_agent
        self._caller_id = caller_id
        self._tool_name = ""
        self._armed = False
        self._value = "absent"
        self._carry = b""
        self._gzip: zlib.decompressobj | None = None
        self._failed = False
        self._status = 0

    def arm(self, tool_name: str) -> None:
        """Mark this request as a ``tools/call`` and keep the tool name."""
        self._armed = True
        self._tool_name = tool_name

    def emit(self, logger: Any) -> None:
        """Write the citeable line when this request was a ``tools/call``.

        A redirect has no tool result. Skipping 3xx keeps the slash-less
        mount from writing an ``isError=absent`` row beside the real call.
        """
        if not self._armed or 300 <= self._status < 400:
            return
        logger.info(
            "%s",
            tools_call_log_line(
                path=self._path,
                tool_name=self._tool_name,
                is_error=self.finish(),
                protocol=self._protocol,
                user_agent=self._user_agent,
                caller_id=self._caller_id,
            ),
        )

    def note_status(self, status: int) -> None:
        """Remember the HTTP status so a redirect is not logged as a tool call."""
        self._status = status

    def note_start(self, headers: list[tuple[bytes, bytes]]) -> None:
        """Remember ``Content-Encoding`` so later chunks inflate before the scan."""
        if not self._armed:
            return
        encoding = ""
        for name, value in headers:
            if name.lower() == b"content-encoding":
                encoding = value.decode("latin1", errors="replace").lower()
                break
        if "gzip" in encoding:
            self._gzip = zlib.decompressobj(16 + zlib.MAX_WBITS)

    def feed(self, chunk: bytes) -> None:
        """Scan one response chunk. A gzip failure sticks at ``unreadable``."""
        if not self._armed or self._failed or not chunk:
            return
        if self._gzip is not None:
            try:
                chunk = self._gzip.decompress(chunk)
            except zlib.error:
                self._failed = True
                self._value = "unreadable"
                return
        self._scan(chunk)

    def finish(self) -> str:
        """Flush a gzip trailer, then return the last ``isError`` seen."""
        if self._failed:
            return self._value
        if self._gzip is not None:
            try:
                self._scan(self._gzip.flush())
            except zlib.error:
                self._failed = True
                self._value = "unreadable"
        return self._value

    def _scan(self, data: bytes) -> None:
        if not data:
            return
        buf = self._carry + data
        matches = list(_IS_ERROR_RE.finditer(buf))
        if matches:
            self._value = matches[-1].group(1).decode("ascii")
        self._carry = buf[-_CARRY_BYTES:]


def _log_field(value: str) -> str:
    text = " ".join((value or "").replace('"', "").split())
    if not text:
        return "absent"
    text = text[:_FIELD_LIMIT]
    if " " in text:
        return f'"{text}"'
    return text
