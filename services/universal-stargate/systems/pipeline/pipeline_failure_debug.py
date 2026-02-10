"""
Write step failure tracebacks and response details to a debug file.

When a pipeline step fails (e.g. ProxyClientError for malformed response),
writes a single debug file per failure under LOG_DIR/pipeline_failures/
so the failing response and full traceback can be inspected without
searching request snapshots.
"""

from __future__ import annotations

import json
import os
import traceback
from datetime import datetime
from pathlib import Path
from typing import cast

from universal_logging import get_logger

logger = get_logger(__name__)

FAILURES_DIR_NAME = "pipeline_failures"


def _get_failures_dir() -> Path:
    """Return pipeline_failures directory under LOG_DIR (or default)."""
    log_dir = os.environ.get("LOG_DIR", "logs")
    out = Path(log_dir) / FAILURES_DIR_NAME
    out.mkdir(parents=True, exist_ok=True)
    return out


def _serialize_detail(detail: object) -> str:
    """Serialize exception detail for JSON (e.g. raw response dict)."""
    if detail is None:
        return "null"
    try:
        return json.dumps(detail, indent=2, default=str)
    except (TypeError, ValueError):
        return repr(detail)


def write_failure_debug(
    pipeline_id: str,
    execution_id: str,
    step_id: str,
    error: Exception,
) -> Path | None:
    """
    Write a debug file for a pipeline step failure.

    File contains: timestamp, pipeline_id, execution_id, step_id,
    exception type/message, full traceback, and if the error is
    ProxyClientError then status_code and detail (e.g. raw Stargate
    response that failed to parse).

    Args:
        pipeline_id: Pipeline identifier.
        execution_id: Execution run identifier.
        step_id: Step that failed.
        error: The exception that was raised.

    Returns:
        Path to the written file, or None if writing failed (logged).
    """
    try:
        failures_dir = _get_failures_dir()
        exc_name = type(error).__name__
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        exec_short = (execution_id or "unknown")[:8]
        safe_step = (step_id or "unknown").replace("/", "_")
        filename = f"{ts}_{pipeline_id}_{safe_step}_{exec_short}.txt"
        path = failures_dir / filename

        lines = [
            f"pipeline_id: {pipeline_id}",
            f"execution_id: {execution_id}",
            f"step_id: {step_id}",
            f"exception: {exc_name}: {error}",
            "",
            "--- TRACEBACK ---",
            traceback.format_exc(),
        ]

        status_code = getattr(error, "status_code", None)
        if status_code is not None:
            lines.append("")
            lines.append(f"status_code: {status_code}")
        detail = getattr(error, "detail", None)
        if detail is not None:
            lines.append("")
            lines.append("--- DETAIL (e.g. raw response) ---")
            lines.append(_serialize_detail(cast(object, detail)))

        content = "\n".join(lines)
        _ = path.write_text(content, encoding="utf-8")
        logger.warning(
            "Pipeline step failure debug file written: %s (step=%s, %s)",
            path,
            step_id,
            exc_name,
        )
        return path
    except Exception as e:
        logger.error(
            "Failed to write pipeline failure debug file: %s", e, exc_info=True
        )
        return None
