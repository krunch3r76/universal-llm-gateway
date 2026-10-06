"""Validate root and stamp run deadline."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, override

from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

from . import _clients

_ROOT_RE = re.compile(r"^\d+$")


class MaestroInductResolveHandler(BaseHandler):
    step_type = "maestro_induct_resolve_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        options: dict[str, Any] = dict(getattr(context, "options", {}) or {})
        pipeline_opts = {}
        req = getattr(context, "original_request", None) or {}
        if isinstance(req, dict):
            pipeline_opts = req.get("pipeline_options") or {}
        options = {**pipeline_opts, **options}
        root = str(options.get("root") or "").strip()
        errors: list[dict[str, str]] = []
        skipped: list[str] = []
        if not _ROOT_RE.match(root):
            errors.append({"kind": "invalid_root", "message": root or "missing"})
        started = _clients.now_epoch()
        started_at = datetime.fromtimestamp(started, UTC).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        payload = {
            "root": root if _ROOT_RE.match(root) else None,
            "options": {
                "journal_entries": int(options.get("journal_entries", 2)),
                "include_runbook": bool(options.get("include_runbook", True)),
                "include_lanes": bool(options.get("include_lanes", True)),
            },
            "started_epoch": started,
            "started_at": started_at,
            "deadline_epoch": started + _clients.RUN_BUDGET_S,
            "errors": errors,
            "skipped": skipped,
            "invalid_root": not _ROOT_RE.match(root),
        }
        if not options.get("include_runbook", True):
            skipped.append("runbook")
        if not options.get("include_lanes", True):
            skipped.append("lanes")
        payload["skipped"] = skipped
        return StepOutput(raw="", json=payload)
