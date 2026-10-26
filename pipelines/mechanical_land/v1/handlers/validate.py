"""Validate mechanical-land pipeline options."""

from __future__ import annotations

import json
import re
from typing import Any, override

from mechanical_land.policy import POLICY_TOP_LEVEL_KEYS, validate_policy_override
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

_SOURCE_REF_RE = re.compile(r"^(todo|agent-bus|friction):[\w./-]+$")
_VALID_KINDS = frozenset({"implement", "evidence"})
_VALID_OPS = frozenset({"start", "transition"})


def evaluate_validate(opts: dict[str, Any]) -> dict[str, Any]:
    op = str(opts.get("op") or "")
    if op not in _VALID_OPS:
        return _reject(422, "op", "invalid_op", f"op must be one of {_VALID_OPS}")
    kind = opts.get("kind")
    if op == "start":
        if kind not in _VALID_KINDS:
            return _reject(422, "kind", "invalid_kind", "unknown kind")
        source_ref = str(opts.get("source_ref") or "")
        if kind == "implement":
            if not _SOURCE_REF_RE.match(source_ref):
                return _reject(422, "source_ref", "invalid_source_ref", "grammar")
            todo = opts.get("todo") or {}
            files_expected = todo.get("files_expected") or opts.get("files_expected")
            ac = todo.get("acceptance_criteria") or opts.get("acceptance_criteria")
            if not files_expected or not ac:
                return _reject(
                    422,
                    "source_ref",
                    "source_ref_unsettled",
                    "todo lacks files_expected or acceptance_criteria",
                )
        policy = opts.get("policy")
        if isinstance(policy, dict):
            try:
                validate_policy_override(policy)
            except Exception as exc:
                code = getattr(exc, "code", "policy_invalid")
                detail = str(exc)
                return _reject(422, "policy", code, detail)
            unknown = set(policy) - POLICY_TOP_LEVEL_KEYS
            if unknown:
                return _reject(
                    422,
                    "policy",
                    "policy_invalid",
                    f"unknown policy keys: {sorted(unknown)}",
                )
    return {"admitted": True, "phase": "validate", "op": op, "kind": kind}


def _reject(status_code: int, field: str, code: str, reason: str) -> dict[str, Any]:
    return {
        "admitted": False,
        "phase": "validate",
        "status_code": status_code,
        "field": field,
        "code": code,
        "reason": reason,
    }


class MechanicalLandValidateHandler(BaseHandler):
    step_type = "mechanical_land_validate_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        opts = dict(getattr(context, "options", {}) or {})
        payload = evaluate_validate(opts)
        return StepOutput(raw=json.dumps(payload), json=payload)
