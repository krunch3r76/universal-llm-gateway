"""Implement closeout handler — run_adapters orchestration."""

from __future__ import annotations

import json
from typing import Any, override

from implement_admission.closeout import apply_closeout
from implement_admission.closeout_models import ImplementCloseout
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput
from universal_logging import get_logger

logger = get_logger(__name__)


class ImplementCloseoutApplyHandler(BaseHandler):
    step_type = "implement_closeout_apply_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        opts = getattr(context, "options", {}) or {}
        closeout_raw = opts.get("closeout")
        if not isinstance(closeout_raw, dict):
            err = {
                "ok": False,
                "error": "missing required pipeline_options.closeout dict",
            }
            return StepOutput(raw=json.dumps(err), json=err, error=err["error"])

        closeout = ImplementCloseout.model_validate(closeout_raw)
        if opts.get("source_ref"):
            closeout = closeout.model_copy(update={"source_ref": opts["source_ref"]})

        try:
            reconciled = apply_closeout(closeout)
        except Exception as exc:
            err = {"ok": False, "error": str(exc)}
            logger.warning("implement_closeout failed: %s", exc)
            return StepOutput(raw=json.dumps(err), json=err, error=str(exc))

        payload = reconciled.model_dump(mode="json")
        payload["ok"] = reconciled.status.value != "failed"
        logger.info(
            "implement_closeout: source_ref=%s status=%s adapters=%d",
            reconciled.source_ref,
            reconciled.status.value,
            len(reconciled.adapter_results),
        )
        return StepOutput(raw=json.dumps(payload, default=str), json=payload)
