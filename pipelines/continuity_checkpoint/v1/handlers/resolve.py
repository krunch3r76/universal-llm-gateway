"""Resolve checkpoint window for continuity CHECKPOINT (cursor + claude_ai)."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, override
from urllib.parse import urlparse

from chat_harvest.models import ClassifyRefuse, classify_chat_url
from continuity_tape.events import stargate_continuity_checkpoint_admitted
from cortex_store.transcript_assembly import _transcripts_root
from cortex_store.transcript_session_id import jsonl_path_for_uuid
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

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
    """Resolve jsonl_path via explicit path or transcript_id (no discover default)."""

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

        # a:33211 — discover dominant_write / leftover explicit_cp is how a
        # foreign hop tab's JSONL sealed onto another house. Bind the caller's
        # id, or refuse. New tabs are often absent from open_windows.
        if not transcript_id:
            return _refused(
                "checkpoint.transcript_id_required",
                "cursor checkpoint requires transcript_id (or jsonl_path)",
            )

        root = _transcripts_root()
        path = jsonl_path_for_uuid(root, str(transcript_id))
        if not path.is_file():
            return _refused(
                "checkpoint.window_unresolvable",
                f"transcript_id {transcript_id!r} has no JSONL",
            )
        try:
            rel = str(path.relative_to(root))
        except ValueError:
            rel = str(path)
        payload = {
            "thread": thread,
            "surface": surface,
            "from_agent": from_agent,
            "jsonl_path": rel,
            "transcript_id": str(transcript_id),
        }
        return StepOutput(raw=json.dumps(payload), json=payload)
