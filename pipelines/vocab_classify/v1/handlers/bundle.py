"""Merge per-scope rows for vocab_classify pipeline (fan-in).

Zips load_hints scope rows with retrieve_samples outputs into a consolidated
array of scope bundles for per-scope classification.
"""

from __future__ import annotations

import json
from typing import Any, ClassVar, TYPE_CHECKING

from systems.pipeline.core.execution.map_reduce import MapOutputCollection
from systems.pipeline.core.handlers.protocol import PipelineContext, StepOutput

if TYPE_CHECKING:
    from systems.pipeline.core.schemas import StepConfig


class VocabClassifyBundleV1Handler:
    """Zip parallel map outputs into per-scope bundles for classification."""

    step_type = "vocab_classify_bundle_v1"
    dependency_fields: ClassVar[tuple[str, ...]] = ("bundle_upstream_steps",)

    async def execute(self, step: StepConfig, context: PipelineContext) -> StepOutput:
        hints_scopes = context.get_output("load_hints")
        if hints_scopes is None or not isinstance(hints_scopes.json, dict):
            raise ValueError("bundle: missing load_hints.json")
        rows: list[dict[str, Any]] = list(hints_scopes.json.get("scopes") or [])
        if not rows:
            payload: dict[str, Any] = {"scopes": []}
            raw = json.dumps(payload)
            return StepOutput(raw=raw, json=payload)

        samples = context.get_output("retrieve_samples")
        if not isinstance(samples, MapOutputCollection):
            raise TypeError("bundle: retrieve_samples must be a map collection")

        s_out = samples.all_outputs()
        n = len(rows)
        if len(s_out) != n:
            raise ValueError(
                f"bundle: length mismatch hints={n} samples={len(s_out)}"
            )

        merged: list[dict[str, Any]] = []
        for i in range(n):
            base = dict(rows[i])
            base["sample_retrieval"] = s_out[i].json or {}
            merged.append(base)

        payload = {"scopes": merged}
        raw = json.dumps(payload, ensure_ascii=False)
        return StepOutput(raw=raw, json=payload)
