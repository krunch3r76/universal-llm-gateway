"""Deterministic lesson parse step for journal-projector."""

from __future__ import annotations

from typing import Any, override

from claude_bundles.journal_lesson import capture_lesson
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

from .projector_guard import reject_projector_wire_model


class JournalProjectorParseHandler(BaseHandler):
    step_type = "journal_projector_parse_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        opts = getattr(context, "options", {}) or {}
        turn_id = str(opts.get("source_turn_id") or opts.get("turn_id") or "").strip()
        body = str(opts.get("disposition_body") or "").strip()
        if not body:
            messages = getattr(context, "messages", None) or []
            for msg in reversed(messages):
                if isinstance(msg, dict) and msg.get("role") == "user":
                    content = msg.get("content")
                    if isinstance(content, str) and content.strip():
                        body = content.strip()
                        break
        if not body:
            return StepOutput(
                raw="",
                json={"has_lesson": False, "reason": "empty_body"},
            )

        capture = capture_lesson(body, turn_id=turn_id or "unknown")
        if capture is None:
            return StepOutput(
                raw="",
                json={"has_lesson": False, "reason": "no_line_start_lesson"},
            )

        model_override = opts.get("model")
        if isinstance(model_override, str) and model_override.strip():
            reject_projector_wire_model(model_override.strip())

        return StepOutput(
            raw=capture.lesson_line,
            json={
                "has_lesson": True,
                "turn_id": capture.turn_id,
                "lesson_line": capture.lesson_line,
            },
        )
