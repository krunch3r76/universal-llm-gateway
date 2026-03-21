"""Per-scope reconciliation for vocab_classify pipeline.

Collects per-scope classify outputs (each containing a single scope's
classification) and assembles the final vocabulary + scope_hashes payload
for persistence via data_sink.
"""

from __future__ import annotations

import json
from typing import Any, ClassVar, TYPE_CHECKING

from systems.pipeline.core.execution.map_reduce import MapOutputCollection
from systems.pipeline.core.handlers.protocol import PipelineContext, StepOutput
from universal_logging import get_logger

if TYPE_CHECKING:
    from systems.pipeline.core.schemas import StepConfig

_REGISTERS = ("practitioner", "academic", "specification")

logger = get_logger(__name__)


class VocabClassifyReconcileV1Handler:
    """Assemble per-scope classify outputs into vocabulary + scope_hashes."""

    step_type = "vocab_classify_reconcile_v1"
    dependency_fields: ClassVar[tuple[str, ...]] = ("reconcile_inputs",)

    async def execute(self, step: StepConfig, context: PipelineContext) -> StepOutput:
        bundle_out = context.get_output("bundle")
        if bundle_out is None or not isinstance(bundle_out.json, dict):
            raise ValueError("reconcile: missing bundle.json")

        bundle_scopes: list[dict[str, Any]] = list(
            bundle_out.json.get("scopes") or []
        )
        mode = str(context.options.get("mode") or "frontier")

        if not bundle_scopes:
            payload: dict[str, Any] = {
                "vocabulary": {},
                "scope_hashes": {},
                "provenance": [],
            }
            return StepOutput(raw=json.dumps(payload), json=payload)

        classify_col = context.get_output("classify")
        if not isinstance(classify_col, MapOutputCollection):
            if isinstance(classify_col, StepOutput) and (
                classify_col.json or {}
            ).get("_skipped"):
                raise ValueError(
                    "reconcile: classify was skipped but scopes need classification"
                )
            raise TypeError("reconcile: classify must be a map collection")

        scope_hashes: dict[str, str] = {}
        for row in bundle_scopes:
            scope = str(row.get("scope") or "")
            fh = row.get("files_hash")
            if scope and isinstance(fh, str) and fh.strip():
                scope_hashes[scope] = fh.strip()

        scope_terms_src: dict[str, list[str]] = {}
        for row in bundle_scopes:
            scope = str(row.get("scope") or "")
            terms = row.get("terms") or []
            if scope and isinstance(terms, list):
                scope_terms_src[scope] = [str(t).strip() for t in terms if str(t).strip()]

        vocabulary: dict[str, dict[str, list[str]]] = {}
        provenance: list[dict[str, Any]] = []

        for out in classify_col.all_outputs():
            parsed = out.json
            if not isinstance(parsed, dict):
                continue
            scope = str(parsed.get("scope") or "")
            if not scope:
                logger.warning("reconcile: classify output missing 'scope' field")
                continue

            reg_lists: dict[str, list[str]] = {r: [] for r in _REGISTERS}
            for reg in _REGISTERS:
                terms = parsed.get(reg) or []
                if not isinstance(terms, list):
                    continue
                for t in terms:
                    if isinstance(t, str) and t.strip():
                        reg_lists[reg].append(t.strip())

            vocabulary[scope] = reg_lists
            model_id = out.model_id or "unknown"

            for reg in _REGISTERS:
                for term in reg_lists[reg]:
                    provenance.append(
                        {
                            "scope": scope,
                            "term": term,
                            "register": reg,
                            "confidence": "single_model",
                            "mode": mode,
                            "votes": {model_id: reg},
                        }
                    )

        payload = {
            "vocabulary": vocabulary,
            "scope_hashes": scope_hashes,
            "provenance": provenance,
        }
        return StepOutput(
            raw=json.dumps(payload, ensure_ascii=False), json=payload
        )
