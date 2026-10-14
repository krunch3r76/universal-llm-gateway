"""Pipeline handler wrapping append_journal_from_closeout."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, override

from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

from .append_closeout import append_journal_from_closeout, read_closeout_file


class JournalProjectorAppendHandler(BaseHandler):
    step_type = "journal_projector_append_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        opts = getattr(context, "options", {}) or {}
        dispatch_id = str(opts.get("dispatch_id") or "").strip()
        if not dispatch_id:
            return StepOutput(
                raw="",
                json={"ok": False, "error": "dispatch_id required in pipeline_options"},
            )

        repo_root = opts.get("repo_root")
        if repo_root:
            root = Path(str(repo_root))
        else:
            root = Path(os.environ.get("GIT_INTEGRATION_WORKTREE_ROOT", os.getcwd()))

        closeout_text = opts.get("closeout_text")
        if not isinstance(closeout_text, str):
            closeout_text = read_closeout_file(root, dispatch_id)
        if closeout_text is None:
            return StepOutput(
                raw="",
                json={"ok": False, "error": "closeout_missing", "dispatch_id": dispatch_id},
            )

        turn_id = opts.get("source_turn_id")
        result = append_journal_from_closeout(
            closeout_text,
            turn_id=str(turn_id).strip() if turn_id else None,
        )
        payload: dict[str, Any] = {
            "ok": result.ok,
            "skipped": result.skipped,
            "written_sha256": result.written_sha256,
        }
        if result.skipped_reason:
            payload["skipped_reason"] = result.skipped_reason
        if result.error:
            payload["error"] = result.error
        return StepOutput(raw="", json=payload)
