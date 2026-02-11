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
from typing import TypedDict, cast

from universal_logging import get_logger


class _CallContext(TypedDict, total=False):
    """Per-call failure debug: request_id, request_body, response_content."""

    request_id: str | None
    snapshot_request_id: str | None
    request_body: dict[str, object]
    response_content: str | None


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


def _pretty_print_if_json(text: str) -> str:
    """If text is or contains JSON, return pretty-printed form; else return as-is."""
    if not text or not text.strip():
        return text
    stripped = text.strip()
    try:
        if stripped.startswith("{") or stripped.startswith("["):
            return json.dumps(json.loads(text), indent=2, default=str)
    except (json.JSONDecodeError, TypeError):
        pass
    if "\n" in text:
        prefix, _, rest = text.partition("\n")
        rest_pp = _pretty_print_if_json(rest) if rest.strip() else rest
        return f"{prefix}\n{rest_pp}"
    return text


def write_failure_debug(
    pipeline_id: str,
    execution_id: str,
    step_id: str,
    error: Exception,
    *,
    call_contexts: list[_CallContext] | None = None,
) -> Path | None:
    """
    Write a debug file for a pipeline step failure.

    File contains: pipeline_id, execution_id, step_id, request_id(s),
    exception type/message, full traceback, pretty-printed exception
    notes and detail, and optionally per-call request/response bodies.

    Args:
        pipeline_id: Pipeline identifier.
        execution_id: Execution run identifier (pipeline run).
        step_id: Step that failed.
        error: The exception that was raised.
        call_contexts: Optional list of dicts with keys request_id,
            request_body (dict), response_content (str). Used to write
            request_id(s) and pretty-printed request/response bodies.

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

        # Request IDs (match snapshot filenames: {timestamp}_{request_id}.json)
        if call_contexts:
            request_ids = [
                str(c.get("request_id") or c.get("snapshot_request_id") or "")
                for c in call_contexts
            ]
            request_ids = [rid for rid in request_ids if rid]
            if request_ids:
                lines.append("")
                lines.append("--- REQUEST IDS (for snapshot lookup) ---")
                for rid in request_ids:
                    lines.append(rid)

        # Include exception notes (e.g., full model responses); pretty-print JSON
        notes = getattr(error, "__notes__", None)
        if notes:
            lines.append("")
            lines.append("--- EXCEPTION NOTES ---")
            for note in notes:
                lines.append(_pretty_print_if_json(str(note)))

        status_code = getattr(error, "status_code", None)
        if status_code is not None:
            lines.append("")
            lines.append(f"status_code: {status_code}")
        detail = getattr(error, "detail", None)
        if detail is not None:
            lines.append("")
            lines.append("--- DETAIL (e.g. raw response) ---")
            lines.append(_serialize_detail(cast(object, detail)))

        # Per-call request/response bodies (pretty-printed)
        if call_contexts:
            for i, ctx in enumerate(call_contexts):
                rid = (
                    ctx.get("request_id")
                    or ctx.get("snapshot_request_id")
                    or f"call_{i}"
                )
                lines.append("")
                lines.append(f"--- REQUEST (request_id={rid}) ---")
                req = ctx.get("request_body")
                if req is not None:
                    lines.append(_serialize_detail(req))
                else:
                    lines.append("(none)")
                lines.append("")
                lines.append(f"--- RESPONSE (request_id={rid}) ---")
                resp = ctx.get("response_content")
                if resp is not None:
                    lines.append(_pretty_print_if_json(resp))
                else:
                    lines.append("(none)")

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
