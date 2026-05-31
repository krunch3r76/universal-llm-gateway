"""Consult call history persistence (JSONL event stream)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DEFAULT_HISTORY_PATH = Path("/tmp/consult-history/current.jsonl")

_PIPELINE_SUMMARY_ROOT = Path("/tmp/logs/universal-stargate/pipeline_summaries")
_RECORDER_EVENT_TYPES = {"step_output_captured", "step_started", "step_failed"}

# Legacy local pipeline event stream path used by consult history fallback.
_PIPELINE_EVENTS_PATH = Path("/tmp/pipeline-events/current.jsonl")

# Step names that represent primary LLM calls across all consult pipelines:
# consult/analyze/plan/draft = single-step pipelines; review = second step in planner.
_CONSULT_STEP_NAMES = {"consult", "analyze", "plan", "draft", "review"}


@dataclass(slots=True, kw_only=True)
class PipelineStepRecord:
    """One recorder event (step_output_captured / step_started / step_failed)."""

    execution_id: str
    pipeline_id: str
    step_name: str
    event_type: str
    model_id: str | None = None
    raw: str | None = None
    json_data: dict[str, Any] | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: float = 0.0
    system_prompt: str | None = None
    user_prompt: str | None = None
    request_body: dict[str, Any] | None = None
    error: str | None = None
    wall_clock: str | None = None


def find_pipeline_recorder_file(*, execution_id: str, pipeline_id: str) -> Path | None:
    """Locate the recorder events.jsonl for a given execution.

    Searches ``_PIPELINE_SUMMARY_ROOT/<pipeline_id>/*_<exec_short>/events.jsonl``
    and returns the most-recently modified match.  Returns None when the
    pipeline_summaries directory does not exist or no matching directory is found.
    """
    exec_short = execution_id[:8]
    pipeline_dir = _PIPELINE_SUMMARY_ROOT / pipeline_id
    if not pipeline_dir.exists():
        return None
    matches = sorted(
        pipeline_dir.glob(f"*_{exec_short}/events.jsonl"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return matches[0] if matches else None


def extract_pipeline_step_records(
    *, execution_id: str, pipeline_id: str
) -> list[PipelineStepRecord]:
    """Extract recorder step records for consult-relevant steps.

    Reads the recorder events.jsonl file for the given execution and returns
    PipelineStepRecord objects for event_types in ``_RECORDER_EVENT_TYPES``
    whose step_name is a member of ``_CONSULT_STEP_NAMES``.

    Returns an empty list when no recorder file is found or on read errors.
    """
    recorder_file = find_pipeline_recorder_file(
        execution_id=execution_id,
        pipeline_id=pipeline_id,
    )
    if recorder_file is None:
        return []

    records: list[PipelineStepRecord] = []
    try:
        with recorder_file.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(event, dict):
                    continue
                event_type = str(event.get("event_type", ""))
                if event_type not in _RECORDER_EVENT_TYPES:
                    continue
                step_name = str(event.get("step_name", ""))
                if step_name not in _CONSULT_STEP_NAMES:
                    continue
                records.append(
                    PipelineStepRecord(
                        execution_id=execution_id,
                        pipeline_id=pipeline_id,
                        step_name=step_name,
                        event_type=event_type,
                        model_id=event.get("model_id"),
                        raw=event.get("raw"),
                        json_data=event.get("json_data"),
                        prompt_tokens=int(event.get("prompt_tokens") or 0),
                        completion_tokens=int(event.get("completion_tokens") or 0),
                        latency_ms=float(event.get("latency_ms") or 0.0),
                        system_prompt=event.get("system_prompt"),
                        user_prompt=event.get("user_prompt"),
                        request_body=event.get("request_body"),
                        error=event.get("error"),
                        wall_clock=event.get("wall_clock"),
                    )
                )
    except OSError:
        # Log the error for observability
        # import logging
        # logging.error(f"Error reading recorder file {recorder_file}: {e}")
        return []
    return records


def resolve_pipeline_models(
    *,
    execution_id: str | None = None,
    started_at: float | None = None,
    finished_at: float | None = None,
) -> list[str]:
    """Find actual models assigned during a pipeline execution.

    Correlates by execution_id when available (exact match on
    pipeline.step.started/completed/model.resolved events). Falls back to a
    POSIX time window (started_at/finished_at from time.time()) scanning all
    three signal types so that in-flight calls show the selected model as soon
    as StepStarted or StepModelResolved is emitted — before inference finishes.

    Returns distinct model IDs for primary LLM steps (consult, analyze,
    review, plan, draft). Empty list if the pipeline events file is absent.
    """
    if not _PIPELINE_EVENTS_PATH.exists():
        return []

    use_execution_id = execution_id is not None
    started_dt: datetime | None = None
    finished_dt: datetime | None = None
    if not use_execution_id and started_at is not None and finished_at is not None:
        started_dt = datetime.fromtimestamp(started_at, UTC)
        finished_dt = datetime.fromtimestamp(finished_at, UTC)

    # pipeline.step.model.resolved carries the post-selection concrete model ID and
    # is emitted before inference begins — preferred over step.started (which may
    # carry a static YAML default) and step.completed (only available post-inference).
    step_signals = {
        "pipeline.step.started",
        "pipeline.step.completed",
        "pipeline.step.model.resolved",
    }

    models: list[str] = []
    seen: set[str] = set()
    try:
        with _PIPELINE_EVENTS_PATH.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(ev, dict):
                    continue
                signal = ev.get("signal")
                if signal not in step_signals:
                    continue
                payload = ev.get("payload", {})
                if not isinstance(payload, dict):
                    continue

                if use_execution_id:
                    if payload.get("execution_id") != execution_id:
                        continue
                elif started_dt is not None and finished_dt is not None:
                    ts_raw = str(ev.get("timestamp", ""))
                    try:
                        ts_dt = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
                    except ValueError:
                        continue
                    if not (started_dt <= ts_dt <= finished_dt):
                        continue
                else:
                    continue

                step = str(payload.get("step_name", ""))
                if step not in _CONSULT_STEP_NAMES:
                    continue
                model_id = payload.get("model_id")
                if isinstance(model_id, str) and model_id and model_id not in seen:
                    seen.add(model_id)
                    models.append(model_id)
    except OSError:
        # Log the error for observability
        # import logging
        # logging.error(f"Error reading pipeline events file {_PIPELINE_EVENTS_PATH}: {e}")
        return []
    return models


def write_consult_call_started_event(
    *,
    role: str,
    mode: str,
    question: str,
    selected_models: list[str],
    pipeline_id: str | None,
    context_files: list[str],
    cloud_only: bool,
    call_id: str,
    execution_id: str | None = None,
    artifact_dir: str | None = None,
) -> Path:
    """Append a consult.call.started event immediately after model selection.

    Allows consult-history --follow to show in-flight calls before they finish.
    The call_id must match the corresponding consult.call.finished event so
    follow mode can correlate start→finish pairs.
    """
    history_path = resolve_history_path()
    history_path.parent.mkdir(parents=True, exist_ok=True)

    payload: dict[str, Any] = {
        "call_id": call_id,
        "role": role,
        "mode": mode,
        "question_preview": question.strip().replace("\n", " ")[:200],
        "selected_models": selected_models,
        "pipeline_id": pipeline_id,
        "context_files": context_files,
        "context_file_count": len(context_files),
        "cloud_only": cloud_only,
    }
    if execution_id:
        payload["execution_id"] = execution_id
    if artifact_dir:
        payload["artifact_dir"] = artifact_dir

    event: dict[str, Any] = {
        "type": "consult_event",
        "signal": "consult.call.started",
        "timestamp": _now_iso(),
        "payload": payload,
    }

    with history_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=True) + "\n")

    return history_path


def resolve_history_path() -> Path:
    """Resolve history file path, allowing test/runtime override."""
    configured = os.getenv("CONSULT_HISTORY_FILE", "").strip()
    if configured:
        return Path(configured)
    return DEFAULT_HISTORY_PATH


def _now_iso() -> str:
    """Return current UTC timestamp in ISO-8601 Z form."""
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def write_consult_call_event(
    *,
    role: str,
    mode: str,
    question: str,
    selected_models: list[str],
    used_models: list[str],
    selection_path: str | None,
    pipeline_id: str | None,
    context_files: list[str],
    output_file: str | None,
    cloud_only: bool,
    success: bool,
    error: str | None,
    duration_seconds: float,
    call_id: str,
    execution_id: str | None = None,
    status: str | None = None,
    artifact_dir: str | None = None,
    partial_output_available: bool = False,
    chain_phase_count: int | None = None,
    failure_kind: str | None = None,
) -> Path:
    """Append one consult-call completion event to the history stream."""
    history_path = resolve_history_path()
    history_path.parent.mkdir(parents=True, exist_ok=True)

    payload: dict[str, Any] = {
        "call_id": call_id,
        "role": role,
        "mode": mode,
        "question_preview": question.strip().replace("\n", " ")[:200],
        "selected_models": selected_models,
        "used_models": used_models,
        "selection_path": selection_path,
        "pipeline_id": pipeline_id,
        "context_files": context_files,
        "context_file_count": len(context_files),
        "output_file": output_file,
        "cloud_only": cloud_only,
        "success": success,
        "error": error,
        "duration_seconds": round(duration_seconds, 3),
    }
    if execution_id:
        payload["execution_id"] = execution_id
    if status is not None:
        payload["status"] = status
    if artifact_dir is not None:
        payload["artifact_dir"] = artifact_dir
    if partial_output_available:
        payload["partial_output_available"] = partial_output_available
    if chain_phase_count is not None:
        payload["chain_phase_count"] = chain_phase_count
    if failure_kind is not None:
        payload["failure_kind"] = failure_kind

    event: dict[str, Any] = {
        "type": "consult_event",
        "signal": "consult.call.finished",
        "timestamp": _now_iso(),
        "payload": payload,
    }

    with history_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=True) + "\n")

    return history_path
