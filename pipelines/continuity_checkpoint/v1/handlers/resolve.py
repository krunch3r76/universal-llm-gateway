"""Resolve transcript jsonl_path for continuity CHECKPOINT (cursor surface)."""

from __future__ import annotations

import json
import logging
from typing import Any, override

from continuity_tape.events import stargate_continuity_checkpoint_admitted
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

from ._clients import cortex_dispatch

logger = logging.getLogger(__name__)


def _refused(code: str, message: str) -> StepOutput:
    payload = {"refused": {"code": code, "message": message}}
    return StepOutput(raw=json.dumps(payload), json=payload)


class ContinuityCheckpointResolveHandler(BaseHandler):
    """Resolve jsonl_path via explicit path, transcript_id, or discover."""

    step_type = "continuity_checkpoint_resolve_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        options: dict[str, Any] = getattr(context, "options", {}) or {}
        thread = str(options.get("thread") or context.dispatch_thread_id or "")
        surface = str(options.get("surface") or "cursor")
        from_agent = str(options.get("from_agent") or "continuity")
        execution_id = str(context.execution_id or "")

        stargate_continuity_checkpoint_admitted(
            execution_id=execution_id,
            thread=thread,
            surface=surface,
            from_agent=from_agent,
        )

        if surface == "claude_ai":
            # Phase 3: classify_chat_url(chat_url) reopen criterion
            return _refused(
                "checkpoint.surface_not_landed",
                "claude_ai checkpoint leg blocked until Phase 3 harvest lands",
            )

        if surface != "cursor":
            return _refused("checkpoint.surface_not_landed", f"unsupported surface {surface!r}")

        jsonl_path = options.get("jsonl_path")
        transcript_id = options.get("transcript_id")

        if jsonl_path:
            payload = {
                "thread": thread,
                "surface": surface,
                "from_agent": from_agent,
                "jsonl_path": str(jsonl_path),
                "transcript_id": transcript_id,
            }
            return StepOutput(raw=json.dumps(payload), json=payload)

        discover_args: dict[str, Any] = {"thread": thread}
        if transcript_id:
            discover_args["explicit_transcript_ids"] = [str(transcript_id)]

        discover = await cortex_dispatch("transcript_discover", discover_args)
        if discover.get("error"):
            return _refused(
                str(discover.get("code") or "checkpoint.window_unresolvable"),
                str(discover.get("error")),
            )

        windows = discover.get("open_windows") or []
        explicit = [w for w in windows if w.get("binding") == "explicit_cp"]
        dominant = [w for w in windows if w.get("binding") == "dominant_write"]

        if transcript_id:
            match = [w for w in windows if w.get("transcript_id") == transcript_id]
            if not match:
                return _refused(
                    "checkpoint.window_unresolvable",
                    f"transcript_id {transcript_id!r} not in discover open_windows",
                )
            chosen = match[0]
        elif len(explicit) == 1:
            chosen = explicit[0]
        elif len(dominant) == 1:
            chosen = dominant[0]
        elif len(dominant) >= 2:
            return _refused(
                "checkpoint.window_unresolvable",
                f"{len(dominant)} dominant_write candidates; pass transcript_id",
            )
        elif len(windows) == 1:
            chosen = windows[0]
        else:
            return _refused(
                "checkpoint.window_unresolvable",
                "no resolvable open window for checkpoint",
            )

        payload = {
            "thread": thread,
            "surface": surface,
            "from_agent": from_agent,
            "jsonl_path": chosen.get("jsonl_path"),
            "transcript_id": chosen.get("transcript_id"),
            "session_id_hint": chosen.get("session_id"),
        }
        return StepOutput(raw=json.dumps(payload), json=payload)
