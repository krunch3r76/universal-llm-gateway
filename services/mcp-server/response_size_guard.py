"""Response size guard middleware for the MCP server.

Intercepts oversized tool responses at the FastMCP middleware layer, stores
the complete untruncated result in a process-local in-memory dictionary, and
returns a compact reference that consumers can exchange via the ``retrieve``
tool. Profile-specific byte thresholds are read from the existing
``request_profile`` contextvar so cursor_safe and default profiles each get
appropriate limits.

Threading model: ``threading.Lock`` protects the shared store. FastMCP runs
sync tools via ``anyio.to_thread.run_sync()``, so concurrent tool calls may
access the store from different threads. ``asyncio.Lock`` is forbidden by
project policy; ``threading.Lock`` is the correct primitive here.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from fastmcp.server.middleware.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools.tool import ToolResult
from mcp_events import record
from request_profile import current_profile
from tool_access import CURSOR_SAFE_PROFILE

if TYPE_CHECKING:
    import mcp.types as mt
    from fastmcp import FastMCP

logger = logging.getLogger(__name__)

_TTL_SECONDS: float = 600.0
_CAPACITY: int = 50

_CURSOR_THRESHOLD: int = int(os.getenv("MCP_CURSOR_RESPONSE_SIZE_LIMIT", str(32 * 1024)))
_DEFAULT_THRESHOLD: int = int(os.getenv("MCP_DEFAULT_RESPONSE_SIZE_LIMIT", str(128 * 1024)))
_OVERRIDE_THRESHOLD: int | None = (
    int(os.environ["MCP_RESPONSE_SIZE_LIMIT"])
    if "MCP_RESPONSE_SIZE_LIMIT" in os.environ
    else None
)
_DISABLED: bool = os.getenv("MCP_RESPONSE_SIZE_GUARD_DISABLE", "").strip().lower() in {
    "1",
    "true",
    "yes",
}


@dataclass(slots=True)
class _StoredResponse:
    """In-memory entry holding an oversized tool result awaiting consumer retrieval."""

    result: ToolResult
    tool_name: str
    size_bytes: int
    stored_at: float
    profile: str


_store: dict[str, _StoredResponse] = {}
_lock: threading.Lock = threading.Lock()


@dataclass(slots=True)
class _ExpiredEvent:
    """Payload fields needed to emit a response-expired event after lock release."""

    tool_name: str
    ref_id: str
    profile: str
    size_bytes: int
    age_s: float


def _threshold_for_profile() -> int:
    """Return the byte threshold for the active request profile.

    Global override via ``MCP_RESPONSE_SIZE_LIMIT`` takes precedence.
    Otherwise ``cursor_safe`` gets a tighter limit than the default profile
    to protect the Cursor extension host from oversized JSON-RPC bodies.
    """
    if _OVERRIDE_THRESHOLD is not None:
        return _OVERRIDE_THRESHOLD
    if current_profile() == CURSOR_SAFE_PROFILE:
        return _CURSOR_THRESHOLD
    return _DEFAULT_THRESHOLD


def _measure_result(result: ToolResult) -> int:
    """Serialize a ToolResult to JSON and return the byte length.

    This matches the shape FastMCP serializes for the wire. Only called
    when the result is potentially oversized; small results skip this via
    the structured_content fast-path estimate in the middleware.
    """
    return len(
        json.dumps(result.model_dump(mode="json", exclude_none=True)).encode("utf-8")
    )


def _generate_ref_id() -> str:
    """Produce a collision-free opaque reference ID for the in-memory store.

    Format: ``rs_<6 hex chars>``. Retries under the lock if a collision occurs
    (probability ~3e-9 with 50 entries; retry is a safety net, not expected).
    """
    while True:
        ref_id = f"rs_{secrets.token_hex(3)}"
        if ref_id not in _store:
            return ref_id


def _prune_expired(now: float) -> list[_ExpiredEvent]:
    """Remove store entries older than ``_TTL_SECONDS`` and return expiry events.

    Caller must hold ``_lock``. Events are emitted after lock release so
    event publishing never blocks other store operations.
    """
    events: list[_ExpiredEvent] = []
    expired = [(k, v) for k, v in _store.items() if (now - v.stored_at) > _TTL_SECONDS]
    for key, entry in expired:
        del _store[key]
        events.append(
            _ExpiredEvent(
                tool_name=entry.tool_name,
                ref_id=key,
                profile=entry.profile,
                size_bytes=entry.size_bytes,
                age_s=round(now - entry.stored_at, 1),
            )
        )
    return events


def _emit_expired_events(events: list[_ExpiredEvent]) -> None:
    """Emit response-expired events captured during lock-protected store mutation."""
    for event in events:
        record(
            "mcp.response.expired",
            tool_name=event.tool_name,
            ref_id=event.ref_id,
            profile=event.profile,
            size_bytes=event.size_bytes,
            age_s=event.age_s,
        )


def _store_result(tool_name: str, result: ToolResult, size: int) -> str:
    """Commit an oversized tool result to the in-memory store and return its reference ID.

    Prunes expired entries first. If the store is at capacity after pruning,
    evicts the oldest entry. The result object is stored without any truncation
    or transformation.
    """
    now = time.monotonic()
    profile = current_profile()
    expired_events: list[_ExpiredEvent]
    with _lock:
        expired_events = _prune_expired(now)
        if len(_store) >= _CAPACITY:
            oldest_key = min(_store, key=lambda k: _store[k].stored_at)
            evicted = _store.pop(oldest_key)
            expired_events.append(
                _ExpiredEvent(
                    tool_name=evicted.tool_name,
                    ref_id=oldest_key,
                    profile=evicted.profile,
                    size_bytes=evicted.size_bytes,
                    age_s=round(now - evicted.stored_at, 1),
                )
            )
        ref_id = _generate_ref_id()
        _store[ref_id] = _StoredResponse(
            result=result,
            tool_name=tool_name,
            size_bytes=size,
            stored_at=now,
            profile=profile,
        )
    _emit_expired_events(expired_events)
    return ref_id


def _replacement_result(ref_id: str, tool_name: str, size: int, threshold: int) -> ToolResult:
    """Build the compact reference ToolResult returned for oversized responses.

    Contains a natural-language message with the reference ID and retrieval
    instructions. Any MCP consumer (LLM or programmatic) can parse and follow it.
    """
    note = (
        f"Response exceeded size limit "
        f"({size // 1024}KB > {threshold // 1024}KB threshold).\n"
        f"Stored as: {ref_id} ({size // 1024}KB, expires in 10 min)\n\n"
        f"To retrieve the full response:\n"
        f'  retrieve(id="{ref_id}")'
    )
    return ToolResult(content=note)


class ResponseSizeGuard(Middleware):
    """FastMCP middleware that intercepts oversized tool responses.

    Responses under the profile-specific byte threshold pass through unchanged.
    Oversized responses are stored untruncated in memory and replaced with a
    compact reference that the consumer redeems via the ``retrieve`` tool.
    The ``retrieve`` tool itself is exempt from guarding to prevent recursion.
    """

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        """Intercept tool results, storing oversized ones and returning references."""
        if _DISABLED:
            return await call_next(context)

        result = await call_next(context)
        tool_name = context.message.name

        if tool_name == "retrieve":
            return result

        threshold = _threshold_for_profile()

        if result.structured_content is not None:
            estimate = len(json.dumps(result.structured_content, default=str).encode("utf-8"))
            if estimate < threshold // 2:
                return result

        size = _measure_result(result)
        if size <= threshold:
            return result

        ref_id = _store_result(tool_name, result, size)
        record(
            "mcp.response.guarded",
            tool_name=tool_name,
            profile=current_profile(),
            original_bytes=size,
            threshold_bytes=threshold,
            ref_id=ref_id,
            store_count=len(_store),
        )
        return _replacement_result(ref_id, tool_name, size, threshold)


def register_response_guard(mcp: FastMCP) -> None:
    """Register the retrieve tool and response size guard middleware on the server.

    Must be called after all other tools are registered but before any
    pruning or middleware that depends on the final tool list. The
    ``retrieve`` tool must appear in ``_PRIMARY_TOOLS`` so it survives
    ``_prune_to_primary()`` and is directly visible to all consumers.
    """

    @mcp.tool()
    def retrieve(id: str) -> ToolResult | dict[str, str]:
        """Retrieve a stored oversized tool response by its reference ID.

        When a tool response exceeds the profile-specific byte threshold,
        the response size guard stores the complete untruncated result in
        memory and returns a compact reference. Call this tool with that
        reference ID to claim the full original result.

        Pop semantics: each stored response can be retrieved exactly once.
        Responses expire after 10 minutes if unclaimed.
        """
        now = time.monotonic()
        expired_events: list[_ExpiredEvent]
        with _lock:
            expired_events = _prune_expired(now)
            stored = _store.pop(id, None)
        _emit_expired_events(expired_events)
        if stored is None:
            return {
                "error": (
                    f"No stored response with ID '{id}'. "
                    "It may have expired or already been retrieved."
                )
            }
        record(
            "mcp.response.retrieved",
            tool_name=stored.tool_name,
            ref_id=id,
            profile=stored.profile,
            size_bytes=stored.size_bytes,
            age_s=round(now - stored.stored_at, 1),
        )
        return stored.result

    mcp.add_middleware(ResponseSizeGuard())
    logger.info(
        "Response size guard active (cursor_safe=%dKB, default=%dKB, disabled=%s)",
        _CURSOR_THRESHOLD // 1024,
        _DEFAULT_THRESHOLD // 1024,
        _DISABLED,
    )
