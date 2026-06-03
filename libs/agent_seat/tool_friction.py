"""Deterministic tool-loop friction tracking.

The native loop cannot rely on another model turn to explain why a dispatch
failed. This module classifies tool errors into compact, replay-safe summaries
and applies a few conservative no-progress guards.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from typing import Any, Protocol


class ToolCallLike(Protocol):
    turn: int
    name: str
    arguments: dict[str, Any]
    result: str
    ok: bool
    elapsed_ms: float


@dataclass(slots=True)
class ToolSkip:
    reason: str
    message: str
    suggested_next_action: str


def _parse_json_object(raw: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _inner_error_message(result: str) -> str:
    payload = _parse_json_object(result)
    message = payload.get("error") or payload.get("message") or result
    if isinstance(message, str):
        nested = _parse_json_object(message)
        if nested:
            return str(nested.get("error") or nested.get("message") or message)
        return message
    return str(message)


def _args_hash(arguments: dict[str, Any]) -> str:
    canonical = json.dumps(
        arguments, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return f"args:{hashlib.sha256(canonical.encode()).hexdigest()[:12]}"


# Priority order for extracting a target identifier from inner cortex args.
_CORTEX_TARGET_KEYS = (
    "id",
    "entity_id",
    "slug",
    "source_id",
    "target_id",
    "relationship_id",
    "journal_id",
    "query",
)


def _extract_cortex_target(inner_args: dict[str, Any]) -> str:
    for key in _CORTEX_TARGET_KEYS:
        val = inner_args.get(key)
        if val and isinstance(val, str):
            return val
    return ""


def classify_tool_failure(
    name: str, arguments: dict[str, Any], result: str
) -> dict[str, Any]:
    """Return a stable error class and target for a failed tool call."""
    message = _inner_error_message(result)
    tool = str(arguments.get("tool") or name)
    target = ""
    suggested = "Inspect the tool error and choose a different next action."

    if name == "fs" and arguments.get("op") == "md_read":
        target = f"{arguments.get('path', '')}#{arguments.get('section', '')}"
        if "Section not found" in message:
            return {
                "tool": "fs.md_read",
                "code": "section_not_found",
                "target": target,
                "message": message[:300],
                "suggested_next_action": "Run md_list for the document, then retry with an exact section name.",
            }

    elif name == "cortex":
        inner_tool = arguments.get("tool", "")
        tool = f"cortex.{inner_tool}" if inner_tool else "cortex"
        raw_args = arguments.get("arguments")
        if isinstance(raw_args, str):
            inner_args = _parse_json_object(raw_args)
        elif isinstance(raw_args, dict):
            inner_args = raw_args
        else:
            inner_args = {}
        target = _extract_cortex_target(inner_args)
        if inner_tool == "entity_create" and (
            "HTTP 409" in message or "Entity already exists" in message
        ):
            return {
                "tool": "cortex.entity_create",
                "code": "entity_exists",
                "target": target,
                "message": message[:300],
                "suggested_next_action": "Use entity_get to verify the existing entity, then entity_update if metadata must change.",
            }

    elif name == "observability":
        params = arguments.get("params") or {}
        if not isinstance(params, dict):
            params = {}
        operation = params.get("operation") or arguments.get("operation") or ""
        execution_id = params.get("execution_id") or ""
        tool = f"observability.{operation}" if operation else "observability"
        if not execution_id:
            return {
                "tool": tool,
                "code": "missing_required_argument",
                "target": f"{operation}:execution_id",
                "message": message[:300],
                "suggested_next_action": suggested,
            }
        target = f"{operation}:{execution_id}"

    if not target:
        target = _args_hash(arguments)

    return {
        "tool": tool,
        "code": "tool_error",
        "target": target,
        "message": message[:300],
        "suggested_next_action": suggested,
    }


class ToolFrictionTracker:
    """Track deterministic tool-loop friction across native-loop turns."""

    def __init__(self) -> None:
        # Raw per-key call count — used for informational summaries.
        self._failure_counts: Counter[tuple[str, str, str]] = Counter()
        # Turn numbers per key — distinct-turn set drives the halt predicate.
        self._failure_turns: dict[tuple[str, str, str], list[int]] = {}
        self.exhaustion_reason: str | None = None

    def should_skip(
        self, name: str, arguments: dict[str, Any], *, remaining_turns: int
    ) -> ToolSkip | None:
        return None

    def observe(self, call: ToolCallLike) -> None:
        if call.ok:
            return
        failure = classify_tool_failure(call.name, call.arguments, call.result)
        key = (
            str(failure["tool"]),
            str(failure["code"]),
            str(failure["target"]),
        )
        self._failure_counts[key] += 1
        turns = self._failure_turns.setdefault(key, [])
        turns.append(call.turn)
        # ∀ key: halt iff |distinct_turns| ≥ 2 (same failure across two separate turns).
        if len(set(turns)) >= 2 and self.exhaustion_reason is None:
            self.exhaustion_reason = (
                f"repeated_{failure['code']} from {failure['tool']} "
                f"against {failure['target']!r}"
            )

    @property
    def should_stop(self) -> bool:
        return self.exhaustion_reason is not None

    def build_summary(
        self,
        *,
        execution_id: str | None,
        turns_used: int,
        tool_calls: list[ToolCallLike],
    ) -> dict[str, Any]:
        failed = [call for call in tool_calls if not call.ok]
        grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
        for call in failed:
            failure = classify_tool_failure(call.name, call.arguments, call.result)
            key = (
                str(failure["tool"]),
                str(failure["code"]),
                str(failure["target"]),
            )
            item = grouped.setdefault(
                key,
                {
                    "tool": failure["tool"],
                    "code": failure["code"],
                    "target": failure["target"],
                    "count": 0,
                    "_turns": set(),
                    "first_turn": call.turn,
                    "last_turn": call.turn,
                    "message": failure["message"],
                    "suggested_next_action": failure["suggested_next_action"],
                },
            )
            item["count"] += 1
            item["_turns"].add(call.turn)
            item["last_turn"] = call.turn

        for item in grouped.values():
            item["distinct_turns"] = sorted(item.pop("_turns"))

        successful = [call for call in tool_calls if call.ok]
        return {
            "execution_id": execution_id,
            "turns_used": turns_used,
            "tool_calls_made": len(tool_calls),
            "exhaustion_reason": self.exhaustion_reason or "tool_loop_budget_exhausted",
            "failed_tools": sorted(
                grouped.values(),
                key=lambda item: (-int(item["count"]), int(item["first_turn"])),
            ),
            "last_successful_tool": (
                {
                    "tool": successful[-1].name,
                    "turn": successful[-1].turn,
                    "elapsed_ms": round(successful[-1].elapsed_ms, 1),
                }
                if successful
                else None
            ),
            "suggested_continuation": [
                "Use entity_get/entity_update when entity_create reports entity_exists.",
                "Use md_list before retrying md_read after section_not_found.",
            ],
        }
