"""Deterministic tool-loop friction tracking.

The native loop cannot rely on another model turn to explain why a dispatch
failed. This module classifies tool errors into compact, replay-safe summaries
and applies a few conservative no-progress guards.
"""

from __future__ import annotations

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

    if name == "cortex" and tool == "entity_create":
        args = arguments.get("arguments")
        entity_id = ""
        if isinstance(args, str):
            entity_id = str(_parse_json_object(args).get("id") or "")
        elif isinstance(args, dict):
            entity_id = str(args.get("id") or "")
        target = entity_id
        if "HTTP 409" in message or "Entity already exists" in message:
            return {
                "tool": "cortex.entity_create",
                "code": "entity_exists",
                "target": target,
                "message": message[:300],
                "suggested_next_action": "Use entity_get to verify the existing entity, then entity_update if metadata must change.",
            }

    if name in {"dispatch", "agent_consult"} and tool == "agent_consult":
        suggested = "Skip further consult calls and synthesize from current evidence."

    return {
        "tool": tool,
        "code": "tool_error",
        "target": target or str(arguments.get("path") or arguments.get("tool") or ""),
        "message": message[:300],
        "suggested_next_action": suggested,
    }


class ToolFrictionTracker:
    """Track deterministic tool-loop friction across native-loop turns."""

    def __init__(self) -> None:
        self._failure_counts: Counter[tuple[str, str, str]] = Counter()
        self._consult_calls = 0
        self.exhaustion_reason: str | None = None

    def should_skip(
        self, name: str, arguments: dict[str, Any], *, remaining_turns: int
    ) -> ToolSkip | None:
        tool = str(arguments.get("tool") or name)
        if name in {"dispatch", "agent_consult"} and tool == "agent_consult":
            if self._consult_calls >= 1:
                return ToolSkip(
                    reason="agent_consult_cap",
                    message="agent_consult already ran once in this tool loop.",
                    suggested_next_action="Use the existing consult result or synthesize from current evidence.",
                )
            if remaining_turns <= 2:
                return ToolSkip(
                    reason="agent_consult_final_turn_reserve",
                    message="agent_consult skipped to reserve final turns for synthesis.",
                    suggested_next_action="Summarize current evidence instead of spending the remaining turn budget on consult.",
                )
            self._consult_calls += 1
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
        if self._failure_counts[key] >= 2 and self.exhaustion_reason is None:
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
                    "first_turn": call.turn,
                    "last_turn": call.turn,
                    "message": failure["message"],
                    "suggested_next_action": failure["suggested_next_action"],
                },
            )
            item["count"] += 1
            item["last_turn"] = call.turn

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
                "Avoid further agent_consult calls when turn budget is low.",
            ],
        }
