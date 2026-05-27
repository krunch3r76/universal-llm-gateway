"""
Pipeline execution summary writer — public façade.

``ExecutionSummaryWriter`` owns the on-disk summary lifecycle: it accepts a
pipeline + execution context and writes one of four artifact shapes (JSON,
YAML, single-file markdown, or per-step execution directory). All rendering,
dict construction, and retention enforcement is delegated to sibling modules
within the ``execution_summary`` package; this class is a thin orchestrator.

Public API surface (preserved from the pre-split monolith):
``ExecutionSummaryWriter`` and ``get_summary_writer`` (the latter exported
from ``factory.py``). Helper modules (``summary_dict``, ``retention``,
``markdown.*``) are addressable by full dotted path but are not re-exported
from the package ``__init__.py``.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from universal_logging import get_logger

from ..verification_report import (
    STEP_TYPE_VERIFY_CHAIN_V4,
    build_verification_report,
)
from .markdown import render_full_summary_markdown, render_step_markdown
from .retention import (
    cleanup_all_pipelines as _cleanup_all_pipelines,
)
from .retention import (
    cleanup_old_exec_dirs,
    cleanup_old_summaries,
)
from .summary_dict import build_summary_dict, generate_summary_filename

if TYPE_CHECKING:
    from ..core.handlers.protocol import PipelineContext
    from ..core.schemas import PipelineSpec

logger = get_logger(__name__)


class ExecutionSummaryWriter:
    """
    Writes pipeline execution summaries to disk.

    Instance state: ``output_dir`` (the summaries root) and the
    ``MAX_SUMMARIES_PER_PIPELINE`` class-level retention cap. All writes are
    scoped under ``output_dir / pipeline.id`` (per-pipeline subdirectories).

    Invariants:
        - Every write call ends with a retention sweep for that pipeline.
        - On write failure, the error is logged and re-raised (per
          ``[quality]`` exception-handling discipline).
        - File-shape vs directory-shape outputs are tracked under separate
          retention paths (see ``retention.py``).
    """

    # Retention: number of most recent executions to keep per pipeline.
    MAX_SUMMARIES_PER_PIPELINE = 1

    def __init__(self, output_dir: str | Path = "logs/pipeline_summaries"):
        """
        Initialize the writer and ensure the output directory exists.

        Args:
            output_dir: Directory to write summary files (created on first use).
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
        Write execution summary to a JSON file under the pipeline subdirectory.

        Returns:
            Path to the written summary file.
        """
        timestamp = datetime.now()

        pipeline_dir = self.output_dir / pipeline.id
        pipeline_dir.mkdir(parents=True, exist_ok=True)

        summary = build_summary_dict(
            pipeline, context, request_body, response_body, execution_order, timestamp
        )

        filename = generate_summary_filename(context.execution_id, timestamp)
        filepath = pipeline_dir / filename

        try:
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(summary, f, indent=2, ensure_ascii=False)
            logger.info(f"Pipeline summary written: {filepath}")

            cleanup_old_summaries(
                self.output_dir, pipeline.id, self.MAX_SUMMARIES_PER_PIPELINE
            )

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

        Returns:
            Path to the written YAML file.
        """
        import yaml

        timestamp = datetime.now()

        pipeline_dir = self.output_dir / pipeline.id
        pipeline_dir.mkdir(parents=True, exist_ok=True)

        summary = build_summary_dict(
            pipeline, context, request_body, response_body, execution_order, timestamp
        )

        filename = generate_summary_filename(context.execution_id, timestamp).replace(
            ".json", ".yaml"
        )
        filepath = pipeline_dir / filename

        try:
            with open(filepath, "w", encoding="utf-8") as f:
                yaml.dump(summary, f, default_flow_style=False, allow_unicode=True)
            logger.info(f"Pipeline summary (YAML) written: {filepath}")

            cleanup_old_summaries(
                self.output_dir, pipeline.id, self.MAX_SUMMARIES_PER_PIPELINE
            )

            return filepath
        except Exception as e:
            logger.error(f"Failed to write YAML summary: {e}", exc_info=True)
            raise

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

        Markdown body is produced by ``markdown.render_full_summary_markdown`` —
        the single source of truth shared with the per-step execution
        directory's ``full_summary.md``.

        Returns:
            Path to the written markdown file.
        """
        timestamp = datetime.now()

        pipeline_dir = self.output_dir / pipeline.id
        pipeline_dir.mkdir(parents=True, exist_ok=True)

        content = render_full_summary_markdown(
            pipeline,
            context,
            request_body,
            response_body,
            execution_order,
            timestamp,
        )

        filename = generate_summary_filename(context.execution_id, timestamp).replace(
            ".json", ".md"
        )
        filepath = pipeline_dir / filename

        try:
            with open(filepath, "w", encoding="utf-8") as f:
                _ = f.write(content)
            logger.info(f"Pipeline summary (markdown) written: {filepath}")

            cleanup_old_summaries(
                self.output_dir, pipeline.id, self.MAX_SUMMARIES_PER_PIPELINE
            )

            return filepath
        except Exception as e:
            logger.error(f"Failed to write markdown summary: {e}", exc_info=True)
            raise

    def write_step_summaries(
        self,
        pipeline: PipelineSpec,
        context: PipelineContext,
        request_body: dict[str, Any],
        response_body: dict[str, Any],
        execution_order: list[str] | None = None,
    ) -> Path:
        """
        Write per-step markdown files plus a full summary into a dedicated
        execution directory.

        Layout::

            {pipeline_id}/{timestamp}_{exec_id}/
                00_{step1}.md
                01_{step2}.md
                ...
                full_summary.md
                summary.json
                verification_report.json   (only when verify steps present)

        Returns:
            Path to the execution directory.
        """
        timestamp = datetime.now()
        exec_short = context.execution_id[:8]
        date_str = timestamp.strftime("%Y%m%d_%H%M%S")

        exec_dir = self.output_dir / pipeline.id / f"{date_str}_{exec_short}"
        exec_dir.mkdir(parents=True, exist_ok=True)

        step_specs = {step.id: step for step in pipeline.steps}
        ordered_steps = execution_order or list(context.outputs.keys())

        for i, step_id in enumerate(ordered_steps, 1):
            output = context.outputs.get(step_id)
            spec = step_specs.get(step_id)

            if output is None:
                continue

            step_content = render_step_markdown(
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

        full_summary_path = exec_dir / "full_summary.md"
        full_content = render_full_summary_markdown(
            pipeline=pipeline,
            context=context,
            request_body=request_body,
            response_body=response_body,
            execution_order=execution_order,
            timestamp=timestamp,
        )

        with open(full_summary_path, "w", encoding="utf-8") as f:
            f.write(full_content)

        summary = build_summary_dict(
            pipeline, context, request_body, response_body, execution_order, timestamp
        )
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

        cleanup_old_exec_dirs(
            self.output_dir, pipeline.id, self.MAX_SUMMARIES_PER_PIPELINE
        )

        return exec_dir

    def cleanup_all_pipelines(self) -> None:
        """
        Clean up old summaries for all pipelines on startup.

        Enforces retention for both file-based and directory-based summaries
        across every pipeline subdirectory under ``output_dir``.
        """
        _cleanup_all_pipelines(self.output_dir, self.MAX_SUMMARIES_PER_PIPELINE)
