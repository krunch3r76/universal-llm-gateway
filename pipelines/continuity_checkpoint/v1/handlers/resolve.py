"""Resolve checkpoint window for continuity CHECKPOINT (cursor + claude_ai)."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, override
from urllib.parse import urlparse

from chat_harvest.models import ClassifyRefuse, classify_chat_url
from continuity_tape.events import stargate_continuity_checkpoint_admitted
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

from ._clients import cortex_dispatch

logger = logging.getLogger(__name__)

_CSE_TOKEN_RE = re.compile(r"cse_[A-Za-z0-9]+")


def _refused(code: str, message: str) -> StepOutput:
    payload = {"refused": {"code": code, "message": message}}
    return StepOutput(raw=json.dumps(payload), json=payload)


def _transcript_id_from_chat_url(chat_url: str, classified_id: str = "") -> str | None:
    match = _CSE_TOKEN_RE.search(chat_url)
    if match:
        return match.group(0)
    if classified_id:
        return classified_id
    parts = [p for p in urlparse(chat_url.strip()).path.split("/") if p]
    return parts[-1] if parts else None


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
            chat_url = options.get("chat_url")
            if not chat_url:
                return _refused(
                    "checkpoint.chat_url_required",
                    "chat_url is required for claude_ai surface",
                )
            classified = classify_chat_url(str(chat_url))
            classified_id = (
                classified.conversation_id
                if not isinstance(classified, ClassifyRefuse)
                else ""
            )
            transcript_id = _transcript_id_from_chat_url(str(chat_url), classified_id)
            if isinstance(classified, ClassifyRefuse) and not transcript_id:
                return _refused(
                    "checkpoint.chat_url_unclassified",
                    f"{classified.code}: {classified.reason}",
                )
            if not transcript_id:
                return _refused(
                    "checkpoint.chat_url_unclassified",
                    "could not resolve transcript_id from chat_url",
                )
            payload = {
                "thread": thread,
                "surface": surface,
                "from_agent": from_agent,
                "chat_url": str(chat_url),
                "transcript_id": transcript_id,
            }
            return StepOutput(raw=json.dumps(payload), json=payload)

        if surface != "cursor":
            return _refused(
                "checkpoint.surface_not_landed", f"unsupported surface {surface!r}"
            )

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
