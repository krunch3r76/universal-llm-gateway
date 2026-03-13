"""
Pipeline handler for doc-generate tree-sitter extraction.

Thin adapter around ``doc_extraction`` library — adds pipeline-specific
concerns (event emission, StepOutput wrapping, input validation).
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import TYPE_CHECKING, override

from doc_extraction import extract_subsystem_inventory
from systems.pipeline.core.execution.resolver import NamespaceResolver
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput
from universal_logging import get_logger

from .events import (
    doc_generate_architecture_found,
    doc_generate_architecture_notfound,
    doc_generate_extract_failed,
    doc_generate_extract_success,
    doc_generate_python_empty,
)

if TYPE_CHECKING:
    from systems.pipeline.core.handlers.protocol import PipelineContext
    from systems.pipeline.core.schemas import StepConfig

logger = get_logger(__name__)


def _error_output(step_id: str, message: str) -> StepOutput:
    return StepOutput(
        raw=json.dumps({"error": message}),
        json={"error": message},
        step_id=step_id,
        error=message,
    )


def _publish_event(context: PipelineContext, event: object) -> None:
    proxy = getattr(context, "_proxy", None)
    event_bus = getattr(proxy, "event_bus", None) if proxy else None
    if event_bus is None:
        return
    _ = asyncio.create_task(event_bus.publish_async_nowait(event))


def _repo_root() -> Path:
    """Return repository root regardless of Stargate process cwd."""
    return Path(__file__).resolve().parents[3]


class ExtractDocstringsHandler(BaseHandler):
    """Extract docstring inventory for a subsystem directory."""

    step_type: str = "doc_generate_extract_docstrings"

    @override
    async def execute(
        self,
        step: StepConfig,
        context: PipelineContext,
    ) -> StepOutput:
        start_time = time.monotonic()
        resolver = NamespaceResolver(context)
        inputs = step.handler_inputs or {}

        subsystem_path_value = self._resolve_input(
            resolver, step, "subsystem_path", inputs
        )
        if not isinstance(subsystem_path_value, str):
            msg = (
                "subsystem_path must be a string path, got "
                f"{type(subsystem_path_value).__name__}"
            )
            logger.error("Step '%s': %s", step.id, msg)
            _publish_event(
                context,
                doc_generate_extract_failed(
                    execution_id=context.execution_id,
                    step_id=step.id,
                    subsystem_path=None,
                    reason="invalid_subsystem_path_type",
                    error=msg,
                ),
            )
            return _error_output(step.id, msg)
        subsystem_path_raw = subsystem_path_value.strip()
        if not subsystem_path_raw:
            _publish_event(
                context,
                doc_generate_extract_failed(
                    execution_id=context.execution_id,
                    step_id=step.id,
                    subsystem_path=subsystem_path_raw,
                    reason="empty_subsystem_path",
                    error="subsystem_path is empty",
                ),
            )
            return _error_output(step.id, "subsystem_path is empty")

        workspace_root = _repo_root()
        target_dir = Path(subsystem_path_raw)
        if not target_dir.is_absolute():
            target_dir = workspace_root / target_dir
        target_dir = target_dir.resolve()

        try:
            target_dir.relative_to(workspace_root)
        except ValueError:
            msg = f"subsystem_path outside repository root: {subsystem_path_raw}"
            logger.error("Step '%s': %s", step.id, msg)
            _publish_event(
                context,
                doc_generate_extract_failed(
                    execution_id=context.execution_id,
                    step_id=step.id,
                    subsystem_path=subsystem_path_raw,
                    reason="path_outside_repo_root",
                    error=msg,
                ),
            )
            return _error_output(step.id, msg)

        if not target_dir.exists() or not target_dir.is_dir():
            msg = f"subsystem_path is not a directory: {subsystem_path_raw}"
            logger.error("Step '%s': %s", step.id, msg)
            _publish_event(
                context,
                doc_generate_extract_failed(
                    execution_id=context.execution_id,
                    step_id=step.id,
                    subsystem_path=subsystem_path_raw,
                    reason="path_not_directory",
                    error=msg,
                ),
            )
            return _error_output(step.id, msg)

        result = extract_subsystem_inventory(target_dir, workspace_root)

        if result["file_count"] == 0:
            _publish_event(
                context,
                doc_generate_python_empty(
                    execution_id=context.execution_id,
                    step_id=step.id,
                    subsystem_path=target_dir.as_posix(),
                ),
            )

        if result["existing_doc"]:
            _publish_event(
                context,
                doc_generate_architecture_found(
                    execution_id=context.execution_id,
                    step_id=step.id,
                    architecture_doc_path=result["architecture_doc_path"],
                ),
            )
        else:
            _publish_event(
                context,
                doc_generate_architecture_notfound(
                    execution_id=context.execution_id,
                    step_id=step.id,
                    architecture_doc_path=result["architecture_doc_path"],
                ),
            )

        latency_ms = (time.monotonic() - start_time) * 1000
        logger.info(
            "Step '%s': extracted inventory for %d files (%d classes, %d functions)",
            step.id,
            result["file_count"],
            len(result["classes"]),
            len(result["functions"]),
        )
        _publish_event(
            context,
            doc_generate_extract_success(
                execution_id=context.execution_id,
                step_id=step.id,
                subsystem_path=target_dir.as_posix(),
                file_count=result["file_count"],
                class_count=len(result["classes"]),
                function_count=len(result["functions"]),
            ),
        )
        return StepOutput(
            raw=json.dumps(result, indent=2),
            json=result,
            step_id=step.id,
            latency_ms=latency_ms,
        )

    @override
    def validate(self, step: StepConfig) -> list[str]:
        errors: list[str] = []
        inputs = step.handler_inputs or {}
        if "subsystem_path" not in inputs:
            errors.append(
                f"Step '{step.id}': doc_generate_extract_docstrings requires "
                "'subsystem_path' in handler_inputs"
            )
        return errors
