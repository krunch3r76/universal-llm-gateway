"""Read journal entries newest-first."""

from __future__ import annotations

from typing import Any, override

from maestro_induct.parse import parse_journal_entries
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

from . import _clients


class MaestroInductFetchJournalHandler(BaseHandler):
    step_type = "maestro_induct_fetch_journal_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        resolve = _clients.step_output_json(context.outputs, "resolve")
        if resolve.get("invalid_root"):
            return StepOutput(raw="", json={"skipped": True})
        root = str(resolve.get("root"))
        n = int((resolve.get("options") or {}).get("journal_entries", 2))
        uri = f"cortex://notes/system/threads/{root}-journal.md"
        res = _clients.read_cortex_file(uri)
        if isinstance(res, dict):
            return StepOutput(raw="", json=res)
        text, _sha = res
        entries = parse_journal_entries(text, limit=n)
        return StepOutput(raw="", json={"entries": entries})
