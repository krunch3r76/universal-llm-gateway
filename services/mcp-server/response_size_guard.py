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
from typing import TYPE_CHECKING, Any

from fastmcp.server.middleware.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools.tool import ToolResult
from markdown_sections import SectionError
from markdown_sections import list_sections as md_list_sections
from mcp_events import record
from request_profile import current_profile, current_request_metadata
from tool_access import CURSOR_SAFE_PROFILE

if TYPE_CHECKING:
    import mcp.types as mt
    from fastmcp import FastMCP

logger = logging.getLogger(__name__)

_TTL_SECONDS: float = 600.0
_CAPACITY: int = 50
_REASONING_TARGET_MAX_BYTES: int = int(
    os.getenv("MCP_REASONING_TARGET_MAX_BYTES", str(48 * 1024))
)

_CURSOR_THRESHOLD: int = int(
    os.getenv("MCP_CURSOR_RESPONSE_SIZE_LIMIT", str(32 * 1024))
)
_DEFAULT_THRESHOLD: int = int(
    os.getenv("MCP_DEFAULT_RESPONSE_SIZE_LIMIT", str(128 * 1024))
)
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


def _walk_strings(obj: Any) -> list[str]:
    """Recursively collect all str leaves from a JSON-serializable object."""
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, dict):
        out: list[str] = []
        for v in obj.values():
            out.extend(_walk_strings(v))
        return out
    if isinstance(obj, list):
        out = []
        for v in obj:
            out.extend(_walk_strings(v))
        return out
    return []


def _iter_tool_text(result: ToolResult) -> list[str]:
    """Collect every string in a ToolResult that reaches the wire.

    ToolResult.content is a list of MCP content objects (TextContent, ImageContent,
    etc.) — not a plain str. Iterating .text on each item covers the content list.
    Walking all strings in structured_content covers the full tool-return dict.
    """
    strings: list[str] = []
    content = result.content
    if isinstance(content, str):
        strings.append(content)
    elif isinstance(content, list):
        for item in content:
            text = getattr(item, "text", None)
            if isinstance(text, str):
                strings.append(text)
    if isinstance(result.structured_content, dict):
        strings.extend(_walk_strings(result.structured_content))
    return strings


def _validate_utf8_content(result: ToolResult) -> bool:
    """Return False when any wire-bound string contains invalid UTF-8 sequences.

    Lone surrogates introduced by surrogateescape error handlers survive Python
    string internals but corrupt the MCP wire envelope when serialized. Any
    unexpected shape or error fails closed rather than letting a corrupt
    payload through.
    """
    try:
        for s in _iter_tool_text(result):
            s.encode("utf-8")
    except UnicodeEncodeError:
        return False
    except Exception:
        logger.warning("UTF-8 validator hit unexpected ToolResult shape", exc_info=True)
        record("mcp.response.guard.error", tool_name="<unknown>", phase="validate")
        return False
    return True


def _truncate_text(text: str, limit: int = 80) -> str:
    """Return a single-line preview clipped to *limit* characters."""
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 3] + "..."


def _reasoning_target_bytes(threshold: int) -> int:
    """Soft target below the wire threshold for multi-item reasoning payloads."""
    return max(8 * 1024, min(_REASONING_TARGET_MAX_BYTES, threshold // 3))


def _normalize_optional_int(value: Any) -> int | None:
    """Parse an optional integer from request metadata."""
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _adaptive_limit(
    *,
    size_bytes: int,
    item_count: int,
    threshold_bytes: int,
    requested_limit: int | None = None,
) -> int | None:
    """Return a smaller limit suggestion when a discrete payload is too large."""
    if item_count <= 1 or size_bytes <= 0:
        return None
    target_bytes = _reasoning_target_bytes(threshold_bytes)
    if size_bytes <= target_bytes:
        return None
    scaled = max(1, int(item_count * target_bytes / size_bytes))
    if requested_limit is not None and requested_limit > 0:
        scaled = min(scaled, requested_limit)
    return max(1, scaled)


def _summarize_markdown_sections(text: str, max_sections: int = 5) -> list[str]:
    """Return up to *max_sections* markdown section paths from *text*."""
    if not text.strip():
        return []
    try:
        sections = md_list_sections(text)
    except SectionError:
        return []
    return [
        str(section.get("path", "")).strip()
        for section in sections[:max_sections]
        if str(section.get("path", "")).strip()
    ]


def _agent_bus_items(payload: Any) -> tuple[str, list[dict[str, Any]]]:
    """Classify agent-bus payload shape and normalize it to a list of items."""
    if isinstance(payload, list):
        return "turns", [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return "unknown", []
    if isinstance(payload.get("turns"), list):
        return "turns", [item for item in payload["turns"] if isinstance(item, dict)]
    if isinstance(payload.get("threads"), list):
        return "threads", [
            item for item in payload["threads"] if isinstance(item, dict)
        ]
    if isinstance(payload.get("turn"), dict):
        return "single_turn", [payload["turn"]]
    return "unknown", []


def _rebuild_agent_bus_payload(
    payload: Any, kind: str, items: list[dict[str, Any]]
) -> Any:
    """Reconstruct an agent-bus structured payload after slicing a turn list."""
    if kind == "turns" and isinstance(payload, list):
        return items
    if kind == "turns" and isinstance(payload, dict):
        return {**payload, "turns": items}
    if kind == "single_turn" and isinstance(payload, dict) and items:
        return {**payload, "turn": items[0]}
    return payload


def _try_window_agent_bus_result(
    result: ToolResult, *, threshold: int
) -> ToolResult | None:
    """Apply fetch windowing before the size guard when the payload is still too large.

    Backend ``last`` should bound rows, but oversize threads can still exceed the
    reasoning target when bodies are huge or the relay returned an unbounded set.
    Honor ``agent_bus_last`` from request metadata (and adaptive fallback) by
    slicing the serialized turns **before** measuring for guard/store.
    """
    meta = current_request_metadata()
    agent_bus_tool = str(meta.get("agent_bus_tool") or "").strip()
    if agent_bus_tool not in {"", "fetch"}:
        return None

    payload = result.structured_content
    kind, items = _agent_bus_items(payload)
    if kind not in {"turns", "single_turn"} or len(items) <= 1:
        return None

    size = _measure_result(result)
    effective_limit = _reasoning_target_bytes(threshold)
    if size <= effective_limit:
        return None

    requested = _normalize_optional_int(meta.get("agent_bus_last"))
    window = requested
    if window is None:
        window = _adaptive_limit(
            size_bytes=size,
            item_count=len(items),
            threshold_bytes=threshold,
            requested_limit=None,
        )
    if window is None or len(items) <= window:
        return None

    sliced = items[:window]
    # Drop wire content — it may still carry the pre-window JSON blob and would
    # defeat the size measurement (BUG 1, thread 1154/1163).
    windowed = ToolResult(
        structured_content=_rebuild_agent_bus_payload(payload, kind, sliced),
    )
    if _measure_result(windowed) > effective_limit:
        return None

    record(
        "mcp.agentbus.fetch.windowed_pass_through",
        requested_last=requested or 0,
        applied_last=window,
        turn_count_before=len(items),
        turn_count_after=len(sliced),
        original_bytes=size,
        windowed_bytes=_measure_result(windowed),
    )
    return windowed


def _agent_bus_manifest(
    ref_id: str,
    payload: Any,
    size: int,
    threshold: int,
) -> dict[str, Any]:
    """Build a selective-read manifest for oversized agent-bus payloads."""
    kind, items = _agent_bus_items(payload)
    request_meta = current_request_metadata()
    agent_bus_tool = str(request_meta.get("agent_bus_tool") or "").strip()
    requested_limit = _normalize_optional_int(request_meta.get("agent_bus_last"))
    adaptive_last = None
    if kind in {"turns", "single_turn"}:
        # Per-turn bodies can exceed the guard even when the row count already
        # matches the requested window — do not suggest another last= slice.
        if requested_limit is None or len(items) > requested_limit:
            adaptive_last = _adaptive_limit(
                size_bytes=size,
                item_count=len(items),
                threshold_bytes=threshold,
                requested_limit=requested_limit,
            )
    manifest: dict[str, Any] = {
        "large_payload": True,
        "tool": "agent_bus",
        "kind": kind,
        "size_bytes": size,
        "size_kb": round(size / 1024, 1),
        "threshold_bytes": threshold,
        "threshold_kb": round(threshold / 1024, 1),
        "ref_id": ref_id,
        "reasoning_risk": "large_markdown_thread_payload",
        "prefer_selective_reads": True,
        "full_retrieve_last_resort": f'retrieve(id="{ref_id}")',
        "adaptive_last": adaptive_last,
    }
    if agent_bus_tool:
        manifest["agent_bus_tool"] = agent_bus_tool

    if kind in {"turns", "single_turn"}:
        thread_ids = [
            str(item.get("thread", "")).strip()
            for item in items
            if str(item.get("thread", "")).strip()
        ]
        unique_threads = list(dict.fromkeys(thread_ids))
        manifest["turn_count"] = len(items)
        manifest["thread_ids"] = unique_threads[:5]
        manifest["body_chars_total"] = sum(
            len(str(item.get("body", "") or "")) for item in items
        )
        manifest["turn_samples"] = [
            {
                "thread": str(item.get("thread", "") or ""),
                "turn_number": item.get("turn_number"),
                "subject": _truncate_text(str(item.get("subject", "") or ""), 60),
                "body_chars": len(str(item.get("body", "") or "")),
                "markdown_sections": _summarize_markdown_sections(
                    str(item.get("body", "") or "")
                ),
            }
            for item in items[:5]
        ]
        example_thread = unique_threads[0] if unique_threads else ""
        example_turn = items[0].get("turn_number") if items else None
        selective_options = [
            'agent_bus(tool="threads", arguments=\'{"status":"active"}\')',
        ]
        if adaptive_last is not None and example_thread:
            selective_options.append(
                f'agent_bus(tool="fetch", arguments=\'{{"thread":"{example_thread}","last":{adaptive_last},"compact":true}}\')'
            )
        if example_thread:
            selective_options.append(
                f'agent_bus(tool="fetch", arguments=\'{{"thread":"{example_thread}","last":3,"compact":true}}\')'
            )
        if example_thread and isinstance(example_turn, int) and example_turn > 0:
            selective_options.append(
                f'agent_bus(tool="get", arguments=\'{{"thread":"{example_thread}","turn_number":{example_turn}}}\')'
            )
        manifest["selective_options"] = selective_options
        return manifest

    if kind == "threads":
        thread_ids = [
            str(item.get("id", "") or item.get("thread", "") or "").strip()
            for item in items
            if str(item.get("id", "") or item.get("thread", "") or "").strip()
        ]
        manifest["thread_count"] = len(items)
        manifest["thread_ids"] = thread_ids[:8]
        manifest["thread_samples"] = [
            {
                "thread": str(item.get("id", "") or item.get("thread", "") or ""),
                "subject": _truncate_text(str(item.get("subject", "") or ""), 60),
                "summary": _truncate_text(str(item.get("summary", "") or ""), 80),
                "status": str(item.get("status", "") or ""),
            }
            for item in items[:8]
        ]
        example_thread = thread_ids[0] if thread_ids else ""
        selective_options = [
            'agent_bus(tool="threads", arguments=\'{"status":"active","limit":20}\')',
            'agent_bus(tool="threads", arguments=\'{"status":"active","tags":["project:YOUR_PROJECT"]}\')',
        ]
        if example_thread:
            selective_options.append(
                f'agent_bus(tool="get", arguments=\'{{"thread":"{example_thread}","turn_number":1}}\')'
            )
        selective_options.append(f'retrieve(id="{ref_id}")')
        manifest["selective_options"] = selective_options
        manifest["listing_op"] = True
        return manifest

    manifest["selective_options"] = [f'retrieve(id="{ref_id}")']
    return manifest


def _cortex_items(payload: Any) -> tuple[str, list[dict[str, Any]]]:
    """Classify cortex payload shape and normalize common collections."""
    if not isinstance(payload, dict):
        return "unknown", []

    if any(
        key in payload
        for key in (
            "provisional_entities",
            "flagged_assertions",
            "low_confidence_assertions",
            "thin_descriptions",
        )
    ):
        items: list[dict[str, Any]] = []
        for key in (
            "provisional_entities",
            "flagged_assertions",
            "low_confidence_assertions",
            "thin_descriptions",
        ):
            value = payload.get(key)
            if isinstance(value, list):
                items.extend(item for item in value if isinstance(item, dict))
        return "review_queue", items

    if isinstance(payload.get("items"), list):
        items = [item for item in payload["items"] if isinstance(item, dict)]
        if not items:
            return "collection", items
        first = items[0]
        if "claim" in first or "confidence" in first:
            return "assertions", items
        if "agent" in first and "summary" in first:
            return "journal_read", items
        if {"id", "type", "name"}.issubset(first):
            return "entities", items
        return "collection", items

    if {"id", "type", "name"}.issubset(payload):
        return "entity_get", [payload]

    return "unknown", []


def _cortex_manifest(
    ref_id: str,
    payload: Any,
    size: int,
    threshold: int,
) -> dict[str, Any]:
    """Build a selective-read manifest for oversized cortex payloads."""
    request_meta = current_request_metadata()
    selector = str(request_meta.get("cortex_tool", "") or "").strip()
    kind, items = _cortex_items(payload)
    adaptive_limit = _adaptive_limit(
        size_bytes=size,
        item_count=len(items),
        threshold_bytes=threshold,
        requested_limit=_normalize_optional_int(request_meta.get("cortex_limit")),
    )
    manifest: dict[str, Any] = {
        "large_payload": True,
        "tool": "cortex",
        "kind": kind,
        "cortex_tool": selector or None,
        "size_bytes": size,
        "size_kb": round(size / 1024, 1),
        "threshold_bytes": threshold,
        "threshold_kb": round(threshold / 1024, 1),
        "ref_id": ref_id,
        "reasoning_risk": "large_cortex_knowledge_payload",
        "prefer_selective_reads": True,
        "full_retrieve_last_resort": f'retrieve(id="{ref_id}")',
        "adaptive_limit": adaptive_limit,
    }

    if selector:
        manifest["requested_selector"] = selector

    if kind == "entity_get" and items:
        entity = items[0]
        entity_id = str(
            entity.get("id", "") or request_meta.get("cortex_entity_id", "")
        ).strip()
        assertions = entity.get("assertions")
        relationships = entity.get("relationships")
        manifest["entity"] = {
            "id": entity_id,
            "type": str(entity.get("type", "") or ""),
            "name": _truncate_text(str(entity.get("name", "") or ""), 80),
            "assertion_count": len(assertions) if isinstance(assertions, list) else 0,
            "relationship_count": len(relationships)
            if isinstance(relationships, list)
            else 0,
        }
        selective_options: list[str] = []
        if entity_id:
            selective_options.append(
                f'cortex(tool="assertions", arguments=\'{{"entity_id":"{entity_id}","limit":10}}\')'
            )
            selective_options.append(
                f'cortex(tool="relationships", arguments=\'{{"entity_id":"{entity_id}","limit":10}}\')'
            )
            selective_options.append(
                f'cortex(tool="entity_get", arguments=\'{{"entity_id":"{entity_id}"}}\')'
            )
        manifest["selective_options"] = selective_options or [
            f'retrieve(id="{ref_id}")'
        ]
        return manifest

    if kind == "entities":
        item_type = str(request_meta.get("cortex_type", "") or "")
        if not item_type and items:
            sample_types = {
                str(item.get("type", "") or "")
                for item in items[:10]
                if str(item.get("type", "") or "")
            }
            if len(sample_types) == 1:
                item_type = next(iter(sample_types))
        manifest["item_count"] = len(items)
        if item_type:
            manifest["entity_type"] = item_type
        manifest["entity_samples"] = [
            {
                "id": str(item.get("id", "") or ""),
                "type": str(item.get("type", "") or ""),
                "name": _truncate_text(str(item.get("name", "") or ""), 60),
            }
            for item in items[:8]
        ]
        selective_options = []
        if item_type and adaptive_limit is not None:
            selective_options.append(
                f'cortex(tool="entities", arguments=\'{{"type":"{item_type}","limit":{adaptive_limit}}}\')'
            )
        if item_type:
            selective_options.append(
                f'cortex(tool="entities", arguments=\'{{"type":"{item_type}","limit":10}}\')'
            )
        first_id = str(items[0].get("id", "") or "") if items else ""
        if first_id:
            selective_options.append(
                f'cortex(tool="entity_get", arguments=\'{{"entity_id":"{first_id}"}}\')'
            )
        manifest["request_particular_payload"] = bool(first_id)
        manifest["selective_options"] = selective_options or [
            f'retrieve(id="{ref_id}")'
        ]
        return manifest

    if kind == "assertions":
        manifest["item_count"] = len(items)
        entity_ids = [
            str(item.get("entity_id", "") or "")
            for item in items
            if str(item.get("entity_id", "") or "")
        ]
        unique_entity_ids = list(dict.fromkeys(entity_ids))
        manifest["entity_ids"] = unique_entity_ids[:8]
        manifest["assertion_samples"] = [
            {
                "entity_id": str(item.get("entity_id", "") or ""),
                "confidence": str(item.get("confidence", "") or ""),
                "claim": _truncate_text(str(item.get("claim", "") or ""), 100),
            }
            for item in items[:6]
        ]
        selective_options = []
        if len(unique_entity_ids) == 1 and adaptive_limit is not None:
            selective_options.append(
                f'cortex(tool="assertions", arguments=\'{{"entity_id":"{unique_entity_ids[0]}","limit":{adaptive_limit}}}\')'
            )
        if len(unique_entity_ids) == 1:
            selective_options.append(
                f'cortex(tool="assertions", arguments=\'{{"entity_id":"{unique_entity_ids[0]}","limit":10}}\')'
            )
            selective_options.append(
                f'cortex(tool="entity_get", arguments=\'{{"entity_id":"{unique_entity_ids[0]}"}}\')'
            )
        else:
            if adaptive_limit is not None:
                selective_options.append(
                    f'cortex(tool="assertions", arguments=\'{{"limit":{adaptive_limit}}}\')'
                )
            selective_options.append(
                'cortex(tool="assertions", arguments=\'{"limit":10}\')'
            )
        manifest["selective_options"] = selective_options
        return manifest

    if kind == "journal_read":
        manifest["item_count"] = len(items)
        manifest["journal_samples"] = [
            {
                "timestamp": str(item.get("timestamp", "") or ""),
                "agent": str(item.get("agent", "") or ""),
                "summary": _truncate_text(str(item.get("summary", "") or ""), 100),
            }
            for item in items[:6]
        ]
        options: list[str] = []
        if adaptive_limit is not None:
            options.append(
                f'cortex(tool="journal_read", arguments=\'{{"limit":{adaptive_limit}}}\')'
            )
        options.append('cortex(tool="journal_read", arguments=\'{"limit":3}\')')
        manifest["selective_options"] = options
        return manifest

    if kind == "review_queue":
        provisional = (
            payload.get("provisional_entities", []) if isinstance(payload, dict) else []
        )
        flagged = (
            payload.get("flagged_assertions", []) if isinstance(payload, dict) else []
        )
        manifest["review_queue_counts"] = {
            "provisional_entities": len(provisional)
            if isinstance(provisional, list)
            else 0,
            "flagged_assertions": len(flagged) if isinstance(flagged, list) else 0,
            "low_confidence_assertions": len(
                payload.get("low_confidence_assertions", [])
            )
            if isinstance(payload, dict)
            and isinstance(payload.get("low_confidence_assertions"), list)
            else 0,
            "thin_descriptions": len(payload.get("thin_descriptions", []))
            if isinstance(payload, dict)
            and isinstance(payload.get("thin_descriptions"), list)
            else 0,
        }
        selective_options = [
            'cortex(tool="assertions", arguments=\'{"review_status":"flagged","limit":10}\')',
            'cortex(tool="entities", arguments=\'{"limit":10}\')',
        ]
        if isinstance(provisional, list) and provisional:
            first_id = str(provisional[0].get("id", "") or "")
            if first_id:
                selective_options.append(
                    f'cortex(tool="entity_get", arguments=\'{{"entity_id":"{first_id}"}}\')'
                )
        manifest["selective_options"] = selective_options
        return manifest

    limit_value = request_meta.get("cortex_limit")
    selective_options = []
    if selector and isinstance(limit_value, int) and limit_value > 10:
        selective_options.append(
            f'cortex(tool="{selector}", arguments=\'{{"limit":10}}\')'
        )
    selective_options.append(f'retrieve(id="{ref_id}")')
    manifest["selective_options"] = selective_options
    return manifest


def _replacement_result(
    ref_id: str, tool_name: str, size: int, threshold: int, result: ToolResult
) -> ToolResult:
    """Build the compact reference ToolResult returned for oversized responses.

    Contains a natural-language message with the reference ID and retrieval
    instructions. Any MCP consumer (LLM or programmatic) can parse and follow it.
    """
    if tool_name == "agent_bus":
        payload = result.structured_content
        manifest = _agent_bus_manifest(ref_id, payload, size, threshold)
        note_lines = [
            "Large agent_bus markdown payload flagged.",
            f"Size: {manifest['size_kb']}KB over {manifest['threshold_kb']}KB threshold.",
            f"Stored as: {ref_id} (expires in 10 min).",
        ]
        if manifest.get("adaptive_last") is not None and not manifest.get("listing_op"):
            note_lines.append(
                f"Suggested smaller window: last={manifest['adaptive_last']}."
            )
        if manifest.get("listing_op"):
            note_lines.append(
                "Suggested narrowing: filter threads by status, tags, or limit "
                "(threads() has no last= window — use fetch(thread=...) for turn windows)."
            )
        note_lines.extend(
            [
                "",
                "Prefer selective follow-ups before full retrieval:",
            ]
        )
        for option in manifest.get("selective_options", []):
            note_lines.append(f"- {option}")
        note_lines.extend(
            [
                "",
                "Use full retrieval only as a last resort:",
                f'- retrieve(id="{ref_id}")',
            ]
        )
        return ToolResult(content="\n".join(note_lines), structured_content=manifest)

    if tool_name == "cortex":
        payload = result.structured_content
        manifest = _cortex_manifest(ref_id, payload, size, threshold)
        note_lines = [
            "Large cortex knowledge payload flagged.",
            f"Size: {manifest['size_kb']}KB over {manifest['threshold_kb']}KB threshold.",
            f"Stored as: {ref_id} (expires in 10 min).",
        ]
        if manifest.get("adaptive_limit") is not None:
            note_lines.append(f"Suggested smaller limit: {manifest['adaptive_limit']}.")
        if manifest.get("request_particular_payload"):
            note_lines.append(
                "This is a discrete collection. Request the specific entity or subset you need."
            )
        note_lines.extend(
            [
                "",
                "Prefer selective follow-ups before full retrieval:",
            ]
        )
        for option in manifest.get("selective_options", []):
            if option != f'retrieve(id="{ref_id}")':
                note_lines.append(f"- {option}")
        note_lines.extend(
            [
                "",
                "Use full retrieval only as a last resort:",
                f'- retrieve(id="{ref_id}")',
            ]
        )
        return ToolResult(content="\n".join(note_lines), structured_content=manifest)

    note = (
        f"Response exceeded size limit "
        f"({size // 1024}KB > {threshold // 1024}KB threshold).\n"
        f"Stored as: {ref_id} ({size // 1024}KB, expires in 10 min)\n\n"
        f"To retrieve the full response:\n"
        f'  retrieve(id="{ref_id}")'
    )
    return ToolResult(content=note)


_SEMANTIC_GUARD_TOOLS: frozenset[str] = frozenset({"agent_bus", "cortex"})


class ResponseSizeGuard(Middleware):
    """FastMCP middleware that intercepts oversized tool responses.

    Responses under the profile-specific byte threshold pass through unchanged.
    Oversized responses are stored untruncated in memory and replaced with a
    compact reference that the consumer redeems via the ``retrieve`` tool.
    The ``retrieve`` tool itself is exempt from guarding to prevent recursion.

    For ``agent_bus`` and ``cortex`` tools, a tighter "reasoning target"
    threshold applies: even responses under the hard byte threshold are guarded
    when they exceed the reasoning target, because these tools carry markdown
    payloads that bloat LLM reasoning tokens.
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
        if tool_name == "agent_bus":
            windowed = _try_window_agent_bus_result(result, threshold=threshold)
            if windowed is not None:
                result = windowed

        if not _validate_utf8_content(result):
            record("mcp.response.encoding.rejected", tool_name=tool_name)
            return ToolResult(
                content=(
                    f"Tool {tool_name!r} returned content with invalid UTF-8 sequences; "
                    "response rejected to protect MCP transport integrity."
                )
            )

        reasoning_target = _reasoning_target_bytes(threshold)
        needs_semantic_guard = tool_name in _SEMANTIC_GUARD_TOOLS

        try:
            size = self._estimate_size(result, threshold, needs_semantic_guard)
            if size is None:
                return result

            effective_limit = reasoning_target if needs_semantic_guard else threshold
            if size <= effective_limit:
                return result
        except Exception:
            logger.warning(
                "Guard measurement failed for %s — passing through",
                tool_name,
                exc_info=True,
            )
            record(
                "mcp.response.guard.error",
                tool_name=tool_name,
                phase="measure",
            )
            return result

        try:
            ref_id = _store_result(tool_name, result, size)
        except Exception:
            logger.warning(
                "Guard store failed for %s — passing through",
                tool_name,
                exc_info=True,
            )
            record(
                "mcp.response.guard.error",
                tool_name=tool_name,
                phase="store",
            )
            return result

        record(
            "mcp.response.guarded",
            tool_name=tool_name,
            profile=current_profile(),
            original_bytes=size,
            threshold_bytes=threshold,
            reasoning_target_bytes=reasoning_target,
            ref_id=ref_id,
            store_count=len(_store),
            semantic_guard=needs_semantic_guard and size <= threshold,
        )
        return _replacement_result(ref_id, tool_name, size, threshold, result)

    @staticmethod
    def _estimate_size(
        result: ToolResult,
        threshold: int,
        needs_semantic_guard: bool,
    ) -> int | None:
        """Return measured byte size, or None when the result is safe to pass through.

        For semantic-guard tools (agent_bus, cortex) we always measure fully —
        no fast-path early exit.  For other tools, a quick structured_content
        estimate skips the expensive full serialization when clearly under limit.
        """
        if not needs_semantic_guard and result.structured_content is not None:
            estimate = len(
                json.dumps(result.structured_content, default=str).encode("utf-8")
            )
            if estimate < threshold // 2:
                return None

        return _measure_result(result)


def register_response_guard(mcp: FastMCP) -> None:
    """Register the retrieve tool and response size guard middleware on the server.

    Must be called after all other tools are registered but before any
    pruning or middleware that depends on the final tool list. The
    ``retrieve`` tool must appear in ``_PRIMARY_TOOLS`` so it survives
    ``_prune_to_primary()`` and is directly visible to all consumers.
    """

    @mcp.tool(title="Retrieve Oversized Response")
    def retrieve(id: str) -> ToolResult | dict[str, str]:
        """Retrieve a stored oversized tool response by reference ID (pop semantics, 10min TTL)."""
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
