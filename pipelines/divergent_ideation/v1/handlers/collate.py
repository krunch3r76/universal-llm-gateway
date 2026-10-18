"""collate_v1 handler."""

from __future__ import annotations

import json
from typing import Any, override

from divergent_ideation.collate_v1 import (
    build_run_manifest,
    map_success_fraction,
    merge_parse_failures,
)
from divergent_ideation.events import DivergentIdeationRunCollated
from divergent_ideation.map_enrich_v1 import (
    abstract_briefs_from_map_ideate,
    audits_from_map_audit,
    briefs_from_re_instantiate,
    map_parse_failures_from_collection,
    model_id_mismatches,
)
from systems.pipeline.core.execution.map_reduce.collection import MapOutputCollection
from systems.pipeline.core.execution.resolver import NamespaceResolver
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import PipelineContext, StepOutput
from systems.pipeline.core.schemas import StepConfig


class CollateV1Handler(BaseHandler):
    step_type = "collate_v1"

    @override
    async def execute(self, step: StepConfig, context: PipelineContext) -> StepOutput:
        resolver = NamespaceResolver(context)
        inputs = step.handler_inputs or {}
        payload: dict[str, Any] = {}
        for key in (
            "problem",
            "pool_manifest",
            "leak_report",
            "operators",
            "operator_subsets",
            "operator_dispersion",
            "audit_assignments",
            "run_id",
            "assignment_error",
        ):
            if key in inputs:
                payload[key] = self._resolve_input(resolver, step, key, inputs)

        pool_manifest = list(payload.get("pool_manifest") or [])
        run_id = str(context.execution_id or "")
        briefs_per_model = int(context.options.get("briefs_per_model", 3))
        split_out = context.get_output("operator_split")
        split_json = split_out.json if split_out and isinstance(split_out.json, dict) else {}
        pool_models = list(split_json.get("pool_models") or [])

        parse_failures: list[dict[str, Any]] = list(payload.get("parse_failures") or [])
        map_ideate = context.get_output("map_ideate")
        re_inst = context.get_output("re_instantiate")
        map_audit = context.get_output("map_audit")
        assignments = list(payload.get("audit_assignments") or [])

        abstract_briefs: list[dict[str, Any]] = []
        briefs: list[dict[str, Any]] = []
        audits: list[dict[str, Any]] = []

        if isinstance(map_ideate, MapOutputCollection):
            aligned = map_ideate.outputs_aligned()
            abstract_briefs = abstract_briefs_from_map_ideate(
                aligned, pool_models, pool_manifest, run_id, briefs_per_model
            )
            parse_failures = merge_parse_failures(
                parse_failures,
                map_parse_failures_from_collection(
                    aligned,
                    "map_ideate",
                    pool_models,
                    map_ideate.failed_iteration_hints,
                ),
                model_id_mismatches(aligned, pool_models, "map_ideate"),
            )

        if isinstance(re_inst, MapOutputCollection) and isinstance(
            map_ideate, MapOutputCollection
        ):
            re_successes = map_ideate.all_outputs()
            planned = [str(o.model_id or "") for o in re_successes]
            ideate_positions = (
                list(map_ideate._output_positions)
                if map_ideate._output_positions is not None
                else None
            )
            re_aligned = re_inst.outputs_aligned()
            briefs, re_brief_failures = briefs_from_re_instantiate(
                re_aligned,
                abstract_briefs,
                pool_manifest,
                run_id=run_id,
                ideate_output_positions=ideate_positions,
            )
            parse_failures = merge_parse_failures(
                parse_failures,
                re_brief_failures,
                map_parse_failures_from_collection(
                    re_aligned,
                    "re_instantiate",
                    planned,
                    re_inst.failed_iteration_hints,
                ),
                model_id_mismatches(re_aligned, planned, "re_instantiate"),
            )

        if isinstance(map_audit, MapOutputCollection) and assignments:
            aligned = map_audit.outputs_aligned()
            planned_audit = [str(a.get("model_ref", "")) for a in assignments]
            audits = audits_from_map_audit(aligned, assignments, pool_manifest)
            parse_failures = merge_parse_failures(
                parse_failures,
                map_parse_failures_from_collection(
                    aligned,
                    "map_audit",
                    planned_audit,
                    map_audit.failed_iteration_hints,
                ),
                model_id_mismatches(aligned, planned_audit, "map_audit"),
            )

        payload["abstract_briefs"] = abstract_briefs
        payload["briefs"] = briefs
        payload["audits"] = audits
        payload["parse_failures"] = parse_failures

        skipped_steps: dict[str, bool] = {}
        for name in (
            "retrieve_operators",
            "operator_split",
            "map_ideate",
            "re_instantiate",
            "audit_assign",
            "map_audit",
        ):
            out = context.get_output(name)
            if out and isinstance(out.json, dict) and out.json.get("_skipped"):
                skipped_steps[name] = True
        payload["skipped_steps"] = skipped_steps

        map_failure = False
        for map_step in ("map_ideate", "re_instantiate", "map_audit"):
            out = context.get_output(map_step)
            if not isinstance(out, MapOutputCollection):
                continue
            hints = out.failed_iteration_hints
            total = out._total_count if hasattr(out, "_total_count") else len(out.outputs_aligned())
            if hints is not None and total is not None:
                if map_success_fraction(len(hints), int(total)):
                    map_failure = True
        payload["map_failure"] = map_failure

        problem = payload.get("problem") or {}
        payload["problem_id"] = problem.get("problem_id")
        payload["run_seed"] = problem.get("run_seed")
        payload["benchmark_tag"] = problem.get("benchmark_tag")
        assign_out = context.get_output("audit_assign")
        if assign_out and isinstance(assign_out.json, dict):
            payload.setdefault("assignment_error", assign_out.json.get("assignment_error"))

        payload["run_id"] = run_id
        manifest = build_run_manifest(payload)
        self._publish_bus_event(
            context,
            DivergentIdeationRunCollated(
                convergence_rate=float(manifest.get("convergence_rate") or 0.0),
                falsified=bool(manifest.get("falsified")),
                halted_at=manifest.get("halted_at"),
            ),
        )
        return StepOutput(raw=json.dumps(manifest), json=manifest)
