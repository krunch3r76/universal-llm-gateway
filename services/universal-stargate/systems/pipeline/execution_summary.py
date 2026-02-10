"""
Pipeline execution summary writer.

Captures detailed execution information for debugging and analysis:
- Input request
- All step outputs in execution order
- Final response
- Timing information
- Model metadata

Supports multiple output formats:
- Markdown (default): Human-readable with prominent prompt display
- YAML: Human + machine readable, structured
- JSON: Machine readable, compact
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from universal_logging import get_logger

from .core.execution.map_reduce.collection import MapOutputCollection
from .core.handlers.protocol import StepOutput
from .execution_summary_inputs import (
    format_handler_inputs_section,
    format_map_iteration_inputs,
)
from .verification_report import (
    STEP_TYPE_VERIFY_CHAIN_V4,
    build_verification_report,
)

if TYPE_CHECKING:
    from .core.handlers.protocol import PipelineContext
    from .core.schemas import PipelineSpec

logger = get_logger(__name__)


class ExecutionSummaryWriter:
    """Writes pipeline execution summaries to disk."""

    # Retention: number of most recent executions to keep per pipeline
    MAX_SUMMARIES_PER_PIPELINE = 1  # Default: keep only latest

    def __init__(self, output_dir: str | Path = "logs/pipeline_summaries"):
        """
        Initialize summary writer.

        Args:
            output_dir: Directory to write summary files (created if doesn't exist)
        """
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        logger.info(
            f"Pipeline summary writer initialized: {self.output_dir} "
            f"(retention: {self.MAX_SUMMARIES_PER_PIPELINE} per pipeline)"
        )

    def write_summary(
        self,
        pipeline: PipelineSpec,
        context: PipelineContext,
        request_body: dict[str, Any],
        response_body: dict[str, Any],
        execution_order: list[str] | None = None,
    ) -> Path:
        """
        Write execution summary to file in pipeline-specific directory.

        Args:
            pipeline: Pipeline specification
            context: Pipeline execution context with step outputs
            request_body: Original request body (usually chat completion request)
            response_body: Final response body (OpenAI-compatible format)
            execution_order: Optional list of step IDs in execution order

        Returns:
            Path to written summary file
        """
        timestamp = datetime.now()

        # Create pipeline-specific subdirectory
        pipeline_dir = self.output_dir / pipeline.id
        pipeline_dir.mkdir(parents=True, exist_ok=True)

        # Build summary structure
        summary = {
            "metadata": {
                "pipeline_id": pipeline.id,
                "pipeline_version": pipeline.version,
                "pipeline_type": pipeline.domain,
                "execution_id": context.execution_id,
                "timestamp": timestamp.isoformat(),
                "execution_time_ms": (
                    (datetime.now() - context.started_at).total_seconds() * 1000
                ),
            },
            "request": {
                "source_text": context.source_text,
                "full_request": request_body,
            },
            "execution": self._build_execution_details(
                context, pipeline, execution_order
            ),
            "response": response_body,
            "options": pipeline.options.model_dump(),
        }

        # Generate filename (timestamp and exec_id only, no pipeline_id prefix)
        filename = self._generate_filename(context.execution_id, timestamp)
        filepath = pipeline_dir / filename

        # Write to file
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(summary, f, indent=2, ensure_ascii=False)
            logger.info(f"Pipeline summary written: {filepath}")

            # Cleanup old summaries after successful write
            self._cleanup_old_summaries(pipeline.id)

            return filepath
        except Exception as e:
            logger.error(f"Failed to write pipeline summary: {e}", exc_info=True)
            raise

    def write_summary_yaml(
        self,
        pipeline: PipelineSpec,
        context: PipelineContext,
        request_body: dict[str, Any],
        response_body: dict[str, Any],
        execution_order: list[str] | None = None,
    ) -> Path:
        """
        Write execution summary in YAML format (human + machine readable).

        Args:
            pipeline: Pipeline specification
            context: Pipeline execution context
            request_body: Original request body
            response_body: Final response body
            execution_order: Optional list of step IDs in execution order

        Returns:
            Path to written YAML file
        """
        import yaml

        timestamp = datetime.now()

        # Create pipeline-specific subdirectory
        pipeline_dir = self.output_dir / pipeline.id
        pipeline_dir.mkdir(parents=True, exist_ok=True)

        # Build same summary structure as JSON (reuse _build_execution_details)
        summary = {
            "metadata": {
                "pipeline_id": pipeline.id,
                "pipeline_version": pipeline.version,
                "pipeline_type": pipeline.domain,
                "execution_id": context.execution_id,
                "timestamp": timestamp.isoformat(),
                "execution_time_ms": (
                    (datetime.now() - context.started_at).total_seconds() * 1000
                ),
            },
            "request": {
                "source_text": context.source_text,
                "full_request": request_body,
            },
            "execution": self._build_execution_details(
                context, pipeline, execution_order
            ),
            "response": response_body,
            "options": pipeline.options.model_dump(),
        }

        # Generate filename
        filename = self._generate_filename(context.execution_id, timestamp).replace(
            ".json", ".yaml"
        )
        filepath = pipeline_dir / filename

        try:
            with open(filepath, "w", encoding="utf-8") as f:
                yaml.dump(summary, f, default_flow_style=False, allow_unicode=True)
            logger.info(f"Pipeline summary (YAML) written: {filepath}")

            # Cleanup old summaries
            self._cleanup_old_summaries(pipeline.id)

            return filepath
        except Exception as e:
            logger.error(f"Failed to write YAML summary: {e}", exc_info=True)
            raise

    def _build_execution_details(
        self,
        context: PipelineContext,
        pipeline: PipelineSpec,
        execution_order: list[str] | None,
    ) -> dict[str, Any]:
        """Build detailed execution information."""
        # Get step specs for metadata
        step_specs = {step.id: step for step in pipeline.steps}

        # Build ordered list of step executions
        if execution_order:
            # Use provided execution order
            ordered_steps = execution_order
        else:
            # Fall back to outputs dict order (may not reflect true execution order)
            ordered_steps = list(context.outputs.keys())

        steps_detail = []
        for step_id in ordered_steps:
            output = context.outputs.get(step_id)
            spec = step_specs.get(step_id)

            if output is None:
                continue

            step_info: dict[str, Any] = {
                "step_id": step_id,
                "step_type": spec.type if spec else "unknown",
                "model_id": output.model_id,
                "latency_ms": round(output.latency_ms, 2) if output.latency_ms else 0,
                "raw_output": output.raw,
                "extracted_text": output.text,
                "tokens": {
                    "prompt_tokens": output.prompt_tokens,
                    "completion_tokens": output.completion_tokens,
                    "total_tokens": output.prompt_tokens + output.completion_tokens,
                },
            }

            # Include prompts if captured
            if output.system_prompt or output.user_prompt:
                step_info["prompts"] = {}
                if output.system_prompt:
                    step_info["prompts"]["system"] = output.system_prompt
                if output.user_prompt:
                    step_info["prompts"]["user"] = output.user_prompt

            # Include full request body if captured
            if output.request_body:
                step_info["request_body"] = output.request_body

            # Include json data if available
            if output.json:
                step_info["json"] = output.json

            # Include step configuration
            if spec:
                step_info["config"] = {
                    "model_ref": getattr(spec, "model_ref", None),
                    "prompt_ref": getattr(spec, "prompt_ref", None),
                    "temperature": output.temperature,  # ✅ Reads from output
                    "max_tokens": output.max_tokens,  # ✅ NEW: Include max_tokens
                    "depends_on": getattr(spec, "depends_on", []),
                }

            steps_detail.append(step_info)

        return {
            "step_count": len(steps_detail),
            "steps": steps_detail,
            "final_output_step": pipeline.output
            if hasattr(pipeline, "output")
            else None,
        }

    def _generate_filename(self, execution_id: str, timestamp: datetime) -> str:
        """Generate filename for summary (no pipeline_id prefix)."""
        # Format: YYYYMMDD_HHMMSS_exec-id.json
        date_str = timestamp.strftime("%Y%m%d_%H%M%S")
        exec_short = execution_id[:8]  # First 8 chars of execution ID
        return f"{date_str}_{exec_short}.json"

    def write_summary_markdown(
        self,
        pipeline: PipelineSpec,
        context: PipelineContext,
        request_body: dict[str, Any],
        response_body: dict[str, Any],
        execution_order: list[str] | None = None,
    ) -> Path:
        """
        Write execution summary in markdown format (human-readable).

        Args:
            pipeline: Pipeline specification
            context: Pipeline execution context
            request_body: Original request body
            response_body: Final response body
            execution_order: Optional list of step IDs in execution order

        Returns:
            Path to written markdown file
        """
        timestamp = datetime.now()
        execution_time = (datetime.now() - context.started_at).total_seconds() * 1000

        # Create pipeline-specific subdirectory
        pipeline_dir = self.output_dir / pipeline.id
        pipeline_dir.mkdir(parents=True, exist_ok=True)

        # Build markdown content
        lines = [
            f"# Pipeline Execution Summary: {pipeline.id}",
            "",
            "## Metadata",
            f"- **Pipeline ID**: `{pipeline.id}`",
            f"- **Version**: {pipeline.version}",
            f"- **Type**: {pipeline.domain}",
            f"- **Execution ID**: `{context.execution_id}`",
            f"- **Timestamp**: {timestamp.isoformat()}",
            f"- **Execution Time**: {execution_time:.2f}ms",
            "",
            "## Request",
            "### Source Text",
            "```",
            context.source_text,
            "```",
            "",
            "### Full Request Body",
            "```json",
            json.dumps(request_body, indent=2, ensure_ascii=False),
            "```",
            "",
        ]

        step_specs = {step.id: step for step in pipeline.steps}
        ordered_steps = execution_order or list(context.outputs.keys())
        lines.extend(self._build_token_summary_table(context, ordered_steps))
        lines.extend(["## Execution Steps", ""])

        for i, step_id in enumerate(ordered_steps, 1):
            output = context.outputs.get(step_id)
            spec = step_specs.get(step_id)

            if output is None:
                continue

            # Handle MapOutputCollection (from map steps) vs StepOutput
            is_map_output = isinstance(output, MapOutputCollection)

            if is_map_output:
                # Aggregate from all iterations
                all_outputs = output.all_outputs()
                model_ids = sorted({o.model_id for o in all_outputs if o.model_id})
                model_display = ", ".join(model_ids) if model_ids else "N/A (map step)"
                latencies = [o.latency_ms for o in all_outputs if o.latency_ms]
                latency_display = (
                    f"{sum(latencies):.2f}ms (total, {len(all_outputs)} iterations)"
                    if latencies
                    else "N/A"
                )
                prompt_tokens = sum(o.prompt_tokens for o in all_outputs)
                completion_tokens = sum(o.completion_tokens for o in all_outputs)
            else:
                model_display = output.model_id or "N/A"
                latency_display = (
                    f"{output.latency_ms:.2f}ms" if output.latency_ms else "N/A"
                )
                prompt_tokens = output.prompt_tokens
                completion_tokens = output.completion_tokens

            lines.extend(
                [
                    f"### Step {i}: {step_id}",
                    "",
                    f"- **Type**: {spec.type if spec else 'unknown'}",
                    f"- **Model**: {model_display}",
                    f"- **Latency**: {latency_display}",
                    "",
                ]
            )

            # Display token usage breakdown
            total_tokens = prompt_tokens + completion_tokens
            if total_tokens > 0:
                lines.extend(
                    [
                        "**Token Usage:**",
                        f"- Prompt Tokens: {prompt_tokens}",
                        f"- Completion Tokens: {completion_tokens}",
                        f"- Total Tokens: {total_tokens}",
                        "",
                    ]
                )

            if spec and not is_map_output:
                # Format temperature with precision for readability
                temp_display = (
                    f"{output.temperature:.2f}"
                    if output.temperature is not None
                    else "N/A"
                )
                max_tokens_display = (
                    str(output.max_tokens) if output.max_tokens is not None else "N/A"
                )

                lines.extend(
                    [
                        "**Configuration:**",
                        f"- Model Ref: `{getattr(spec, 'model_ref', 'N/A')}`",
                        f"- Prompt Ref: `{getattr(spec, 'prompt_ref', 'N/A')}`",
                        f"- Temperature: {temp_display}",
                        f"- Max Tokens: {max_tokens_display}",
                        f"- Depends On: {getattr(spec, 'depends_on', [])}",
                        "",
                    ]
                )
            elif spec and is_map_output:
                # Simplified config for map steps
                lines.extend(
                    [
                        "**Configuration:**",
                        f"- Model Ref: `{getattr(spec, 'model_ref', 'N/A')}`",
                        f"- Iterations: {len(output)}",
                        f"- Depends On: {getattr(spec, 'depends_on', [])}",
                        "",
                    ]
                )

            # Display prompts if captured (only for non-map outputs)
            if not is_map_output and (output.system_prompt or output.user_prompt):
                lines.append("**Prompts Sent to Model:**")
                lines.append("")

                if output.system_prompt:
                    lines.extend(
                        [
                            "*System Prompt:*",
                            "```",
                            output.system_prompt,
                            "```",
                            "",
                        ]
                    )

                if output.user_prompt:
                    lines.extend(
                        [
                            "*User Prompt:*",
                            "```",
                            output.user_prompt,
                            "```",
                            "",
                        ]
                    )

            # Display request body if captured (only for non-map outputs)
            if not is_map_output and output.request_body:
                lines.extend(
                    [
                        "**LLM Request Body:**",
                        "```json",
                        json.dumps(output.request_body, indent=2, ensure_ascii=False),
                        "```",
                        "",
                    ]
                )

            # Handle raw/json/text output display
            if is_map_output:
                # For map outputs, show aggregated summary
                all_outputs = output.all_outputs()
                lines.extend(
                    [
                        f"**Map Output:** {len(all_outputs)} iteration(s)",
                        "",
                    ]
                )
                # Show full output (untruncated)
                if all_outputs:
                    sample = all_outputs[0]
                    lines.extend(
                        [
                            "*Full output (first iteration):*",
                            "```json" if sample.raw.strip().startswith("{") else "```",
                            sample.raw,
                            "```",
                            "",
                        ]
                    )
            else:
                lines.extend(
                    [
                        "**Raw Output:**",
                        "```json" if output.raw.strip().startswith("{") else "```",
                        output.raw,
                        "```",
                        "",
                    ]
                )

                if output.json:
                    lines.extend(
                        [
                            "**JSON Data:**",
                            "```json",
                            json.dumps(output.json, indent=2, ensure_ascii=False),
                            "```",
                            "",
                        ]
                    )

                if output.text != output.raw:
                    lines.extend(
                        [
                            "**Extracted Text:**",
                            "```",
                            output.text,
                            "```",
                            "",
                        ]
                    )

        # Add response
        lines.extend(
            [
                "## Final Response",
                "```json",
                json.dumps(response_body, indent=2, ensure_ascii=False),
                "```",
                "",
                "## Pipeline Options",
                "```json",
                json.dumps(pipeline.options.model_dump(), indent=2, ensure_ascii=False),
                "```",
            ]
        )

        # Generate filename (timestamp and exec_id only, no pipeline_id prefix)
        filename = self._generate_filename(context.execution_id, timestamp).replace(
            ".json", ".md"
        )
        filepath = pipeline_dir / filename

        try:
            with open(filepath, "w", encoding="utf-8") as f:
                _ = f.write("\n".join(lines))
            logger.info(f"Pipeline summary (markdown) written: {filepath}")

            # Cleanup old summaries after successful write
            self._cleanup_old_summaries(pipeline.id)

            return filepath
        except Exception as e:
            logger.error(f"Failed to write markdown summary: {e}", exc_info=True)
            raise

    def _build_verification_summary(
        self,
        all_outputs: list[Any],
        step_id: str,
        spec: Any = None,
        context: PipelineContext | None = None,
    ) -> list[str]:
        """
        Build enhanced summary section for verification steps.

        Shows:
        - What statements failed (full text, not just reasons)
        - What statements passed
        - Per-model breakdown statistics
        - Sub-claim/parent relationships

        Args:
            all_outputs: List of verification outputs from each model iteration
            step_id: ID of the verification step
            spec: Step specification (for accessing handler_inputs)
            context: Pipeline context (for resolving statement data)

        Returns:
            List of markdown lines
        """
        # Check if this is a verification step
        is_verification = (
            step_id.startswith("verify") or "verification" in step_id.lower()
        )
        if not is_verification:
            return []

        # Collect all evaluations with model information
        evaluations_with_model = []  # (evaluation, model_id)
        for iter_output in all_outputs:
            if hasattr(iter_output, "json") and isinstance(iter_output.json, dict):
                evals = iter_output.json.get("evaluations", [])
                model_id = getattr(iter_output, "model_id", "unknown")
                for eval_item in evals:
                    evaluations_with_model.append((eval_item, model_id))

        if not evaluations_with_model:
            return []

        # Build statement lookup from handler_inputs if available
        statement_lookup = self._build_statement_lookup(spec, context)

        # Aggregate verdicts by statement_id (across all models)
        stmt_verdicts: dict[str, list[tuple[str, bool, str]]] = defaultdict(
            list
        )  # stmt_id -> [(model, verdict, reason)]
        for eval_item, model_id in evaluations_with_model:
            stmt_id = eval_item.get("statement_id", "unknown")
            verdict = eval_item.get("verdict", False)
            reason = eval_item.get("reason", "")
            stmt_verdicts[stmt_id].append((model_id, verdict, reason))

        # Determine overall pass/fail for each statement (majority vote)
        passed_stmt_ids = set()
        failed_stmt_ids = set()
        for stmt_id, verdicts in stmt_verdicts.items():
            pass_count = sum(1 for _, v, _ in verdicts if v)
            fail_count = len(verdicts) - pass_count
            if pass_count > fail_count:
                passed_stmt_ids.add(stmt_id)
            else:
                failed_stmt_ids.add(stmt_id)

        total_statements = len(stmt_verdicts)
        passed_count = len(passed_stmt_ids)
        pass_rate = (
            (passed_count / total_statements * 100) if total_statements > 0 else 0
        )

        lines = [
            "",
            "## Verification Summary",
            "",
            (
                f"**Overall**: {passed_count}/{total_statements} statements "
                f"passed ({pass_rate:.1f}%)"
            ),
            "",
        ]

        # Per-model breakdown table
        unique_models = sorted(
            {model for _, model in evaluations_with_model if model != "unknown"}
        )
        if len(unique_models) > 1:
            lines.extend(["### Verification by Model", ""])
            lines.append("| Model | Passed | Failed | Pass Rate |")
            lines.append("|-------|--------|--------|-----------|")

            model_stats = {}
            for model in unique_models:
                model_evals = [e for e, m in evaluations_with_model if m == model]
                model_passed = sum(1 for e in model_evals if e.get("verdict", False))
                model_total = len(model_evals)
                model_rate = (
                    (model_passed / model_total * 100) if model_total > 0 else 0
                )
                model_stats[model] = (model_passed, model_total, model_rate)
                # Truncate long model names for table readability
                display_name = model if len(model) <= 30 else model[:27] + "..."
                lines.append(
                    f"| {display_name} | {model_passed}/{model_total} | "
                    f"{model_total - model_passed}/{model_total} | {model_rate:.1f}% |"
                )

            lines.append("")

        # Failed statements section
        if failed_stmt_ids:
            lines.extend(
                [
                    f"### ❌ Failed Verification ({len(failed_stmt_ids)} statements)",
                    "",
                ]
            )

            for stmt_id in sorted(failed_stmt_ids):
                stmt_info = statement_lookup.get(stmt_id, {})
                text = stmt_info.get("text", "")
                is_sub_claim = stmt_info.get("is_sub_claim", False)
                parent_id = stmt_info.get("parent_id")

                # Truncate long statements
                if len(text) > 200:
                    text = text[:200] + "..."

                lines.append(f"**Statement**: {text or f'[{stmt_id}]'}")
                lines.append(f"- **ID**: `{stmt_id}`")

                if is_sub_claim and parent_id:
                    parent_info = statement_lookup.get(parent_id, {})
                    parent_text = parent_info.get("text", parent_id)
                    if len(parent_text) > 150:
                        parent_text = parent_text[:150] + "..."
                    lines.append(f"- **Parent**: {parent_text}")

                # Show per-model verdicts if multiple models
                verdicts = stmt_verdicts[stmt_id]
                if len(unique_models) > 1:
                    verdict_summary = []
                    for model_id, verdict, _ in verdicts:
                        symbol = "✅" if verdict else "❌"
                        # Short model name
                        short_model = (
                            model_id.split(":")[-1] if ":" in model_id else model_id
                        )
                        verdict_summary.append(f"{short_model} {symbol}")
                    lines.append(f"- **Verdicts**: {', '.join(verdict_summary)}")

                # Show one representative reason (from first failing model)
                failed_reasons = [r for _, v, r in verdicts if not v and r]
                if failed_reasons:
                    reason = failed_reasons[0]
                    if len(reason) > 150:
                        reason = reason[:150] + "..."
                    lines.append(f"- **Reason**: {reason}")

                lines.append("")

        # Passed statements section
        if passed_stmt_ids:
            lines.extend(
                [
                    f"### ✅ Passed Verification ({len(passed_stmt_ids)} statements)",
                    "",
                ]
            )

            # Group by type: parent with sub-claims, atomic, sub-claims
            parents_with_subs = []
            atomic_statements = []
            sub_claims = []

            for stmt_id in sorted(passed_stmt_ids):
                stmt_info = statement_lookup.get(stmt_id, {})
                has_sub_claims = stmt_info.get("has_sub_claims", False)
                is_sub_claim = stmt_info.get("is_sub_claim", False)

                if has_sub_claims:
                    parents_with_subs.append(stmt_id)
                elif is_sub_claim:
                    sub_claims.append(stmt_id)
                else:
                    atomic_statements.append(stmt_id)

            # Show parent claims with sub-claims
            if parents_with_subs:
                lines.append(f"**Parent Claims** ({len(parents_with_subs)} passed):")
                for stmt_id in parents_with_subs[:10]:  # Limit to first 10
                    stmt_info = statement_lookup.get(stmt_id, {})
                    text = stmt_info.get("text", stmt_id)
                    if len(text) > 150:
                        text = text[:150] + "..."
                    sub_claim_ids = stmt_info.get("sub_claim_ids", [])
                    lines.append(f"- {text} (`{stmt_id}`)")
                    if sub_claim_ids:
                        passed_subs = sum(
                            1 for sc in sub_claim_ids if sc in passed_stmt_ids
                        )
                        lines.append(
                            f"  - Sub-claims: {passed_subs}/{len(sub_claim_ids)} passed"
                        )
                if len(parents_with_subs) > 10:
                    lines.append(
                        f"\n*... ({len(parents_with_subs) - 10} more parent claims)*"
                    )
                lines.append("")

            # Show atomic statements
            if atomic_statements:
                lines.append(
                    f"**Atomic Statements** ({len(atomic_statements)} passed):"
                )
                for stmt_id in atomic_statements[:10]:  # Limit to first 10
                    stmt_info = statement_lookup.get(stmt_id, {})
                    text = stmt_info.get("text", stmt_id)
                    if len(text) > 150:
                        text = text[:150] + "..."
                    lines.append(f"- {text} (`{stmt_id}`)")
                if len(atomic_statements) > 10:
                    lines.append(f"\n*... ({len(atomic_statements) - 10} more)*")
                lines.append("")

            # Optionally show sub-claims (collapsed by default)
            if sub_claims:
                lines.append(
                    f"**Sub-Claims** ({len(sub_claims)} passed - "
                    "shown with parent context above)"
                )
                lines.append("")

        return lines

    def _build_aggregate_summary(
        self,
        step_id: str,
        output: Any,
    ) -> list[str]:
        """
        Build summary section for aggregate steps: parent rejections (math) with
        authority reasons so summaries explain why without relying on truncated logs.
        """
        if "aggregate" not in step_id.lower():
            return []

        if not getattr(output, "json", None) or not isinstance(output.json, dict):
            return []

        candidates = output.json.get("candidates", [])
        rejected_math = [
            c
            for c in candidates
            if c.get("aggregated_verdict") is False
            and c.get("sub_claim_stats", {}).get("math_strict")
        ]
        if not rejected_math:
            return []

        lines = [
            "",
            "## Aggregate Summary",
            "",
            "### Parent rejections (math)",
            "",
            "Authority (e.g. qwen-math) reasons for each failed sub-claim below.",
            "",
        ]
        for c in rejected_math:
            stmt_id = c.get("statement_id", "unknown")
            text = (c.get("text") or "")[:250]
            if len(c.get("text") or "") > 250:
                text += "..."
            failed_subs = [
                sid
                for sid, sv in (c.get("sub_claim_verdicts") or {}).items()
                if not sv.get("passes")
            ]
            reasons = c.get("failed_sub_reasons") or {}

            lines.append(f"**Parent**: `{stmt_id}`")
            lines.append(f"- **Text**: {text}")
            lines.append(f"- **Failed sub-claims**: {failed_subs}")
            for sid in failed_subs:
                reason = reasons.get(sid, "(no reason in output)")
                if len(reason) > 400:
                    reason = reason[:400] + "..."
                lines.append(f"  - `{sid}`: {reason}")
            lines.append("")

        return lines

    def _build_statement_lookup(
        self, spec: Any, context: PipelineContext | None
    ) -> dict[str, dict]:
        """
        Build lookup table from statement_id to statement data.

        Extracts original statements from handler_inputs to show
        full statement text in verification summaries.

        Args:
            spec: Step specification
            context: Pipeline context

        Returns:
            Dict mapping statement_id to statement dict
        """
        if not spec or not context:
            return {}

        try:
            from .execution_summary_inputs import resolve_handler_inputs

            resolved = resolve_handler_inputs(spec, context)
            if not resolved:
                return {}

            # Look for 'statements' in handler_inputs
            if "statements" in resolved:
                _, statements = resolved["statements"]
                if isinstance(statements, list):
                    lookup = {}
                    for stmt in statements:
                        if isinstance(stmt, dict) and "statement_id" in stmt:
                            lookup[stmt["statement_id"]] = stmt
                    return lookup

        except Exception as e:
            logger.debug(f"Could not build statement lookup: {e}")

        return {}

    def _format_step_markdown(
        self,
        step_id: str,
        step_index: int,
        output: Any,
        spec: Any,
        context: PipelineContext,
    ) -> str:
        """Format a single step as markdown for individual file."""
        is_map_output = isinstance(output, MapOutputCollection)

        # step_index is already 1-based; 0-padded filenames for sorting
        lines = [
            f"# Step {step_index}: {step_id}",
            "",
            "## Metadata",
            f"- **Step ID**: `{step_id}`",
            f"- **Type**: {spec.type if spec else 'unknown'}",
        ]

        if is_map_output:
            all_outputs = output.all_outputs()
            model_ids = sorted({o.model_id for o in all_outputs if o.model_id})
            latencies = [o.latency_ms for o in all_outputs if o.latency_ms]

            lines.extend(
                [
                    f"- **Models**: {', '.join(model_ids)}",
                    f"- **Iterations**: {len(all_outputs)}",
                    f"- **Total Latency**: {sum(latencies):.2f}ms",
                    "",
                ]
            )

            # Handler inputs (common inputs for map step)
            input_lines = format_handler_inputs_section(spec, context)
            if input_lines:
                lines.extend(input_lines)

            # Token usage
            prompt_tokens = sum(o.prompt_tokens for o in all_outputs)
            completion_tokens = sum(o.completion_tokens for o in all_outputs)
            total_tokens = prompt_tokens + completion_tokens

            if total_tokens > 0:
                lines.extend(
                    [
                        "## Token Usage",
                        f"- **Prompt**: {prompt_tokens}",
                        f"- **Completion**: {completion_tokens}",
                        f"- **Total**: {total_tokens}",
                        "",
                    ]
                )

            # Add verification summary if this is a verification step
            verification_summary = self._build_verification_summary(
                all_outputs, step_id, spec, context
            )
            if verification_summary:
                lines.extend(verification_summary)

            # Show each iteration
            lines.extend(["## Iteration Outputs", ""])

            # Get iteration keys if available
            iteration_keys = getattr(output, "_keys", [None] * len(all_outputs))

            for j, iter_output in enumerate(all_outputs):
                latency_str = (
                    f"- Latency: {iter_output.latency_ms:.2f}ms"
                    if iter_output.latency_ms
                    else ""
                )

                # Build iteration header
                iteration_lines = [
                    f"### Iteration {j + 1}",
                    f"- Model: `{iter_output.model_id}`",
                    latency_str,
                    "",
                ]

                # Chunked verification: statements evaluated per model
                iter_json = getattr(iter_output, "json", None)
                if isinstance(iter_json, dict):
                    chunked = iter_json.get("chunked_verification", {})
                    if chunked.get("enabled") and "total_statements" in chunked:
                        total = chunked["total_statements"]
                        iteration_lines.append(f"- **Statements evaluated:** {total}")
                        domains = chunked.get("domains") or {}
                        if domains:
                            parts = [
                                f"{d}: {info.get('statements', 0)}"
                                for d, info in sorted(domains.items())
                            ]
                            iteration_lines.append(
                                f"- **By domain:** {', '.join(parts)}"
                            )
                        iteration_lines.append("")

                # Show per-iteration inputs if available
                iteration_key = iteration_keys[j] if j < len(iteration_keys) else None
                # We don't have access to iteration_value here, so we show what we can
                iter_input_lines = format_map_iteration_inputs(
                    spec, j, None, iteration_key
                )
                if iter_input_lines:
                    iteration_lines.extend(iter_input_lines)

                # Include prompts if captured
                if iter_output.system_prompt or iter_output.user_prompt:
                    iteration_lines.append("**Prompts:**")
                    iteration_lines.append("")

                    if iter_output.system_prompt:
                        iteration_lines.extend(
                            [
                                "*System:*",
                                "```",
                                iter_output.system_prompt,
                                "```",
                                "",
                            ]
                        )

                    if iter_output.user_prompt:
                        iteration_lines.extend(
                            [
                                "*User:*",
                                "```",
                                iter_output.user_prompt,
                                "```",
                                "",
                            ]
                        )

                # Include request body if captured
                if iter_output.request_body:
                    request_json = json.dumps(
                        iter_output.request_body, indent=2, ensure_ascii=False
                    )
                    iteration_lines.extend(
                        [
                            "**LLM Request Body:**",
                            "```json",
                            request_json,
                            "```",
                            "",
                        ]
                    )

                # Add output (prefer raw, fallback to json for non-LLM handlers)
                has_raw = iter_output.raw and iter_output.raw.strip()
                has_json = hasattr(iter_output, "json") and iter_output.json

                if has_raw:
                    # Has meaningful raw output (typical for LLM steps)
                    iteration_lines.extend(
                        [
                            "**Output:**",
                            (
                                "```json"
                                if iter_output.raw.strip().startswith("{")
                                else "```"
                            ),
                            iter_output.raw,
                            "```",
                            "",
                        ]
                    )
                elif has_json:
                    # No raw output, but has JSON data (non-LLM handlers)
                    iteration_lines.extend(
                        [
                            "**Output:**",
                            "```json",
                            json.dumps(iter_output.json, indent=2, ensure_ascii=False),
                            "```",
                            "",
                        ]
                    )
                else:
                    # Truly empty output
                    iteration_lines.extend(
                        [
                            "**Output:**",
                            "```",
                            "",
                            "```",
                            "",
                        ]
                    )

                lines.extend(iteration_lines)

        else:
            latency_str = (
                f"- **Latency**: {output.latency_ms:.2f}ms" if output.latency_ms else ""
            )
            lines.extend(
                [
                    f"- **Model**: `{output.model_id}`",
                    latency_str,
                    "",
                ]
            )

            # Handler inputs
            input_lines = format_handler_inputs_section(spec, context)
            if input_lines:
                lines.extend(input_lines)

            # Token usage
            total_tokens = output.prompt_tokens + output.completion_tokens
            if total_tokens > 0:
                lines.extend(
                    [
                        "## Token Usage",
                        f"- **Prompt**: {output.prompt_tokens}",
                        f"- **Completion**: {output.completion_tokens}",
                        f"- **Total**: {total_tokens}",
                        "",
                    ]
                )

            # Aggregate summary: parent rejections (math) with authority reasons
            aggregate_summary = self._build_aggregate_summary(step_id, output)
            if aggregate_summary:
                lines.extend(aggregate_summary)

            # Configuration
            if spec:
                temp_display = (
                    f"{output.temperature:.2f}"
                    if output.temperature is not None
                    else "N/A"
                )
                max_tokens_display = (
                    str(output.max_tokens) if output.max_tokens is not None else "N/A"
                )

                lines.extend(
                    [
                        "## Configuration",
                        f"- **Model Ref**: `{getattr(spec, 'model_ref', 'N/A')}`",
                        f"- **Prompt Ref**: `{getattr(spec, 'prompt_ref', 'N/A')}`",
                        f"- **Temperature**: {temp_display}",
                        f"- **Max Tokens**: {max_tokens_display}",
                        "",
                    ]
                )

            # Prompts
            if output.system_prompt or output.user_prompt:
                lines.extend(["## Prompts", ""])

                if output.system_prompt:
                    lines.extend(
                        [
                            "### System Prompt",
                            "```",
                            output.system_prompt,
                            "```",
                            "",
                        ]
                    )

                if output.user_prompt:
                    lines.extend(
                        [
                            "### User Prompt",
                            "```",
                            output.user_prompt,
                            "```",
                            "",
                        ]
                    )

            # Request body sent to LLM
            if output.request_body:
                lines.extend(
                    [
                        "## LLM Request Body",
                        "```json",
                        json.dumps(output.request_body, indent=2, ensure_ascii=False),
                        "```",
                        "",
                    ]
                )

            # Output
            lines.extend(
                [
                    "## Output",
                    "",
                    "### Raw",
                    "```json" if output.raw.strip().startswith("{") else "```",
                    output.raw,
                    "```",
                    "",
                ]
            )

            if output.json:
                lines.extend(
                    [
                        "### JSON Data",
                        "```json",
                        json.dumps(output.json, indent=2, ensure_ascii=False),
                        "```",
                        "",
                    ]
                )

        return "\n".join(lines)

    def _build_token_summary_table(
        self, context: PipelineContext, execution_order: list[str]
    ) -> list[str]:
        """Build markdown table summarizing token usage across all steps."""
        lines = [
            "## Pipeline Token Summary",
            "",
            "| Step | Prompt | Completion | Total | Calls |",
            "|------|--------|------------|-------|-------|",
        ]

        total_prompt = 0
        total_completion = 0
        total_calls = 0

        for step_id in execution_order:
            out = context.outputs.get(step_id)
            if out is None:
                continue

            if isinstance(out, MapOutputCollection):
                prompt = sum(o.prompt_tokens for o in out.all_outputs())
                comp = sum(o.completion_tokens for o in out.all_outputs())
                calls = sum(
                    getattr(o, "model_call_count", 0) for o in out.all_outputs()
                )
                if not calls:
                    calls = len(list(out.all_outputs()))
            elif isinstance(out, StepOutput):
                prompt = out.prompt_tokens
                comp = out.completion_tokens
                calls = getattr(out, "model_call_count", 0)
            else:
                continue

            total = prompt + comp
            total_prompt += prompt
            total_completion += comp
            total_calls += calls

            lines.append(f"| {step_id} | {prompt:,} | {comp:,} | {total:,} | {calls} |")

        grand_total = total_prompt + total_completion
        lines.append(
            f"| **TOTAL** | **{total_prompt:,}** | **{total_completion:,}** "
            f"| **{grand_total:,}** | **{total_calls}** |"
        )
        lines.append("")

        return lines

    def _build_full_markdown(
        self,
        pipeline: PipelineSpec,
        context: PipelineContext,
        request_body: dict[str, Any],
        response_body: dict[str, Any],
        execution_order: list[str] | None,
        timestamp: datetime,
    ) -> str:
        """Build full markdown summary (reuses write_summary_markdown logic)."""
        execution_time = (datetime.now() - context.started_at).total_seconds() * 1000

        lines = [
            f"# Pipeline Execution Summary: {pipeline.id}",
            "",
            "## Metadata",
            f"- **Pipeline ID**: `{pipeline.id}`",
            f"- **Version**: {pipeline.version}",
            f"- **Type**: {pipeline.domain}",
            f"- **Execution ID**: `{context.execution_id}`",
            f"- **Timestamp**: {timestamp.isoformat()}",
            f"- **Execution Time**: {execution_time:.2f}ms",
            "",
            "## Request",
            "### Source Text",
            "```",
            context.source_text,
            "```",
            "",
            "### Full Request Body",
            "```json",
            json.dumps(request_body, indent=2, ensure_ascii=False),
            "```",
            "",
        ]

        step_specs = {step.id: step for step in pipeline.steps}
        ordered_steps = execution_order or list(context.outputs.keys())
        lines.extend(self._build_token_summary_table(context, ordered_steps))
        lines.extend(["## Execution Steps", ""])

        for i, step_id in enumerate(ordered_steps, 1):
            output = context.outputs.get(step_id)
            spec = step_specs.get(step_id)

            if output is None:
                continue

            is_map_output = isinstance(output, MapOutputCollection)

            if is_map_output:
                all_outputs = output.all_outputs()
                model_ids = sorted({o.model_id for o in all_outputs if o.model_id})
                model_display = ", ".join(model_ids) if model_ids else "N/A (map step)"
                latencies = [o.latency_ms for o in all_outputs if o.latency_ms]
                latency_display = (
                    f"{sum(latencies):.2f}ms (total, {len(all_outputs)} iterations)"
                    if latencies
                    else "N/A"
                )
                prompt_tokens = sum(o.prompt_tokens for o in all_outputs)
                completion_tokens = sum(o.completion_tokens for o in all_outputs)
            else:
                model_display = output.model_id or "N/A"
                latency_display = (
                    f"{output.latency_ms:.2f}ms" if output.latency_ms else "N/A"
                )
                prompt_tokens = output.prompt_tokens
                completion_tokens = output.completion_tokens

            lines.extend(
                [
                    f"### Step {i}: {step_id}",
                    "",
                    f"- **Type**: {spec.type if spec else 'unknown'}",
                    f"- **Model**: {model_display}",
                    f"- **Latency**: {latency_display}",
                    "",
                ]
            )

            total_tokens = prompt_tokens + completion_tokens
            if total_tokens > 0:
                lines.extend(
                    [
                        "**Token Usage:**",
                        f"- Prompt Tokens: {prompt_tokens}",
                        f"- Completion Tokens: {completion_tokens}",
                        f"- Total Tokens: {total_tokens}",
                        "",
                    ]
                )

            # Aggregate summary: parent rejections (math) with authority reasons
            if not is_map_output:
                aggregate_summary = self._build_aggregate_summary(step_id, output)
                if aggregate_summary:
                    lines.extend(aggregate_summary)

            if spec and not is_map_output:
                temp_display = (
                    f"{output.temperature:.2f}"
                    if output.temperature is not None
                    else "N/A"
                )
                max_tokens_display = (
                    str(output.max_tokens) if output.max_tokens is not None else "N/A"
                )

                lines.extend(
                    [
                        "**Configuration:**",
                        f"- Model Ref: `{getattr(spec, 'model_ref', 'N/A')}`",
                        f"- Prompt Ref: `{getattr(spec, 'prompt_ref', 'N/A')}`",
                        f"- Temperature: {temp_display}",
                        f"- Max Tokens: {max_tokens_display}",
                        f"- Depends On: {getattr(spec, 'depends_on', [])}",
                        "",
                    ]
                )
            elif spec and is_map_output:
                lines.extend(
                    [
                        "**Configuration:**",
                        f"- Model Ref: `{getattr(spec, 'model_ref', 'N/A')}`",
                        f"- Iterations: {len(output)}",
                        f"- Depends On: {getattr(spec, 'depends_on', [])}",
                        "",
                    ]
                )

            if not is_map_output and (output.system_prompt or output.user_prompt):
                lines.append("**Prompts Sent to Model:**")
                lines.append("")

                if output.system_prompt:
                    lines.extend(
                        [
                            "*System Prompt:*",
                            "```",
                            output.system_prompt,
                            "```",
                            "",
                        ]
                    )

                if output.user_prompt:
                    lines.extend(
                        [
                            "*User Prompt:*",
                            "```",
                            output.user_prompt,
                            "```",
                            "",
                        ]
                    )

            if not is_map_output and output.request_body:
                lines.extend(
                    [
                        "**LLM Request Body:**",
                        "```json",
                        json.dumps(output.request_body, indent=2, ensure_ascii=False),
                        "```",
                        "",
                    ]
                )

            if is_map_output:
                all_outputs = output.all_outputs()
                lines.extend(
                    [
                        f"**Map Output:** {len(all_outputs)} iteration(s)",
                        "",
                    ]
                )
                # Per-model statement counts (chunked verification)
                per_model = []
                for o in all_outputs:
                    ojson = getattr(o, "json", None) or {}
                    chunked = ojson.get("chunked_verification", {})
                    n = chunked.get("total_statements")
                    mid = getattr(o, "model_id", None) or "unknown"
                    if n is not None:
                        per_model.append(f"{mid}: {n} statements")
                if per_model:
                    lines.extend(
                        [
                            "**Statements evaluated per model:** "
                            + ", ".join(per_model),
                            "",
                        ]
                    )
                # Show full output (untruncated)
                if all_outputs:
                    sample = all_outputs[0]
                    lines.extend(
                        [
                            "*Full output (first iteration):*",
                            "```json" if sample.raw.strip().startswith("{") else "```",
                            sample.raw,
                            "```",
                            "",
                        ]
                    )
            else:
                lines.extend(
                    [
                        "**Raw Output:**",
                        "```json" if output.raw.strip().startswith("{") else "```",
                        output.raw,
                        "```",
                        "",
                    ]
                )

                if output.json:
                    lines.extend(
                        [
                            "**JSON Data:**",
                            "```json",
                            json.dumps(output.json, indent=2, ensure_ascii=False),
                            "```",
                            "",
                        ]
                    )

                if output.text != output.raw:
                    lines.extend(
                        [
                            "**Extracted Text:**",
                            "```",
                            output.text,
                            "```",
                            "",
                        ]
                    )

        lines.extend(
            [
                "## Final Response",
                "```json",
                json.dumps(response_body, indent=2, ensure_ascii=False),
                "```",
                "",
                "## Pipeline Options",
                "```json",
                json.dumps(pipeline.options.model_dump(), indent=2, ensure_ascii=False),
                "```",
            ]
        )

        return "\n".join(lines)

    def _cleanup_old_exec_dirs(self, pipeline_id: str) -> None:
        """
        Remove old execution directories.

        Keeps only MAX_SUMMARIES_PER_PIPELINE most recent.
        """
        pipeline_dir = self.output_dir / pipeline_id
        if not pipeline_dir.exists():
            return

        # Get all execution directories (skip regular files)
        exec_dirs = sorted(
            [d for d in pipeline_dir.iterdir() if d.is_dir()],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )

        # Keep only MAX_SUMMARIES_PER_PIPELINE most recent
        dirs_to_delete = exec_dirs[self.MAX_SUMMARIES_PER_PIPELINE :]

        for exec_dir in dirs_to_delete:
            try:
                # Remove all files in directory first
                for file_path in exec_dir.iterdir():
                    file_path.unlink()
                exec_dir.rmdir()
                logger.debug(f"Deleted old execution directory: {exec_dir}")
            except Exception as e:
                logger.warning(f"Failed to delete {exec_dir}: {e}")

    def _cleanup_old_summaries(self, pipeline_id: str) -> None:
        """
        Remove old summary files for a pipeline.

        Keeps only MAX_SUMMARIES_PER_PIPELINE most recent files.
        Directories are handled separately by _cleanup_old_exec_dirs().
        """
        pipeline_dir = self.output_dir / pipeline_id
        if not pipeline_dir.exists():
            return

        # Files only (directories handled by _cleanup_old_exec_dirs)
        summary_files = sorted(
            [p for p in pipeline_dir.iterdir() if p.is_file()],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )

        # Keep only MAX_SUMMARIES_PER_PIPELINE most recent
        files_to_delete = summary_files[self.MAX_SUMMARIES_PER_PIPELINE :]

        for filepath in files_to_delete:
            try:
                filepath.unlink()
                logger.debug(f"Deleted old summary: {filepath}")
            except Exception as e:
                logger.warning(f"Failed to delete {filepath}: {e}")

    def write_step_summaries(
        self,
        pipeline: PipelineSpec,
        context: PipelineContext,
        request_body: dict[str, Any],
        response_body: dict[str, Any],
        execution_order: list[str] | None = None,
    ) -> Path:
        """
        Write individual step files plus full summary in execution directory.

        Structure:
            {pipeline_id}/{timestamp}_{exec_id}/
                00_{step1}.md
                01_{step2}.md
                ...
                full_summary.md

        Args:
            pipeline: Pipeline specification
            context: Pipeline execution context
            request_body: Original request body
            response_body: Final response body
            execution_order: Optional list of step IDs in execution order

        Returns:
            Path to execution directory
        """
        timestamp = datetime.now()
        exec_short = context.execution_id[:8]
        date_str = timestamp.strftime("%Y%m%d_%H%M%S")

        # Create execution-specific directory
        exec_dir = self.output_dir / pipeline.id / f"{date_str}_{exec_short}"
        exec_dir.mkdir(parents=True, exist_ok=True)

        # Get step specs and order
        step_specs = {step.id: step for step in pipeline.steps}
        ordered_steps = execution_order or list(context.outputs.keys())

        # Write individual step files (1-based for human readability)
        for i, step_id in enumerate(ordered_steps, 1):
            output = context.outputs.get(step_id)
            spec = step_specs.get(step_id)

            if output is None:
                continue

            step_content = self._format_step_markdown(
                step_id=step_id,
                step_index=i,
                output=output,
                spec=spec,
                context=context,
            )

            step_filename = f"{i:02d}_{step_id}.md"
            step_path = exec_dir / step_filename

            with open(step_path, "w", encoding="utf-8") as f:
                f.write(step_content)

        # Write full summary
        full_summary_path = exec_dir / "full_summary.md"
        full_content = self._build_full_markdown(
            pipeline=pipeline,
            context=context,
            request_body=request_body,
            response_body=response_body,
            execution_order=execution_order,
            timestamp=timestamp,
        )

        with open(full_summary_path, "w", encoding="utf-8") as f:
            f.write(full_content)

        # Write summary.json in exec dir for tools (e.g. extract_verifications_by_model)
        summary = {
            "metadata": {
                "pipeline_id": pipeline.id,
                "pipeline_version": pipeline.version,
                "pipeline_type": pipeline.domain,
                "execution_id": context.execution_id,
                "timestamp": timestamp.isoformat(),
                "execution_time_ms": (
                    (datetime.now() - context.started_at).total_seconds() * 1000
                ),
            },
            "request": {
                "source_text": context.source_text,
                "full_request": request_body,
            },
            "execution": self._build_execution_details(
                context, pipeline, execution_order
            ),
            "response": response_body,
            "options": pipeline.options.model_dump(),
        }
        summary_path = exec_dir / "summary.json"
        with open(summary_path, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)

        execution = summary["execution"]
        steps = execution.get("steps") or []
        has_verify_steps = any(
            s.get("step_type") == STEP_TYPE_VERIFY_CHAIN_V4 for s in steps
        )
        if has_verify_steps:
            metadata = {
                "pipeline_id": pipeline.id,
                "execution_id": context.execution_id,
                "timestamp_iso": summary["metadata"]["timestamp"],
                "source_text": context.source_text,
                "question": context.source_text,
            }
            report = build_verification_report(execution, metadata)
            report_path = exec_dir / "verification_report.json"
            with open(report_path, "w", encoding="utf-8") as f:
                json.dump(report, f, indent=2, ensure_ascii=False)
            logger.info("Verification report written: %s", report_path)

        logger.info(
            f"Per-step summaries written: {exec_dir} "
            f"({len(ordered_steps)} steps + full summary + summary.json)"
        )

        # Cleanup old execution directories
        self._cleanup_old_exec_dirs(pipeline.id)

        return exec_dir

    def cleanup_all_pipelines(self) -> None:
        """
        Clean up old summaries for all pipelines on startup.

        Enforces retention for both file-based summaries and directory-based
        (detailed) summaries.
        """
        if not self.output_dir.exists():
            return

        pipeline_dirs = [d for d in self.output_dir.iterdir() if d.is_dir()]

        for pipeline_dir in pipeline_dirs:
            pipeline_id = pipeline_dir.name
            try:
                # Clean both file-based and directory-based summaries
                self._cleanup_old_summaries(pipeline_id)
                self._cleanup_old_exec_dirs(pipeline_id)
                logger.debug(f"Cleaned up summaries for pipeline: {pipeline_id}")
            except Exception as e:
                logger.warning(f"Failed to cleanup pipeline {pipeline_id}: {e}")

        logger.info(
            f"Startup cleanup complete: {len(pipeline_dirs)} pipeline(s) processed"
        )


def get_summary_writer() -> ExecutionSummaryWriter:
    """
    Get or create global summary writer instance.

    Uses LOG_DIR environment variable if set, otherwise defaults to
    logs/pipeline_summaries.
    """
    log_dir = os.environ.get("LOG_DIR", "logs")
    output_dir = Path(log_dir) / "pipeline_summaries"
    return ExecutionSummaryWriter(output_dir)
