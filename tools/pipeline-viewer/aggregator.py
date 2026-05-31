"""Aggregate pipeline events from JSONL into the viewer data contract.

Reads events.jsonl files written by EventRecorder and produces the same
JSON shape as the old markdown parser (parser.py), so the frontend and
API endpoints remain unchanged.

Also exposes new data the markdown summaries couldn't provide:
- domain_routing: authority verdicts + claim routing decisions
- per-model verdict events with full traceability
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from event_apply import apply_event, infer_verifier_pool

logger = logging.getLogger(__name__)

_LIST_QUESTION_PREVIEW_CHARS = 220


def aggregate_execution(exec_dir: Path) -> dict[str, Any]:
    """Read events.jsonl and produce the viewer data contract.

    Returns dict with: pipeline_id, execution_id, timestamp, started_at_utc, question,
    steps (list), summary (aggregate stats).
    """
    events_file = exec_dir / "events.jsonl"
    if not events_file.exists():
        logger.warning("No events.jsonl in %s", exec_dir)
        return _empty_execution(exec_dir)

    events = _read_events(events_file)
    if not events:
        return _empty_execution(exec_dir)

    # Extract pipeline identity from first event
    first = events[0]
    pipeline_id = first.get("pipeline_id", exec_dir.parent.name)
    execution_id = first.get("execution_id", exec_dir.name)
    started_at_utc = _extract_started_at_utc(events, exec_dir)

    # Parse timestamp from directory name
    dir_name = exec_dir.name
    parts = dir_name.split("_", maxsplit=2)
    timestamp = "_".join(parts[:2]) if len(parts) >= 2 else dir_name

    # Extract question from PipelineStarted event
    question = _extract_question(events)

    # Extract wall-clock duration from PipelineCompleted event
    wall_clock_ms = _extract_wall_clock_ms(events)

    # Build steps from events
    steps = _build_steps(events)

    return {
        "pipeline_id": pipeline_id,
        "execution_id": execution_id,
        "timestamp": timestamp,
        "started_at_utc": started_at_utc,
        "question": question,
        "steps": steps,
        "active_calls": _extract_active_calls(steps),
        "failed_calls": _extract_failed_calls(steps),
        "summary": _build_summary(steps, wall_clock_ms=wall_clock_ms),
    }


def list_executions(summaries_dir: Path) -> list[dict[str, Any]]:
    """List all available executions that have events.jsonl files.

    Scans all pipeline directories and returns executions sorted
    chronologically (newest first) across all pipelines.

    Each entry includes an ``is_live`` flag so the frontend knows whether
    to open an SSE stream or fetch the final aggregate.
    """
    summaries_dir = Path(summaries_dir)
    executions: list[dict[str, Any]] = []

    if not summaries_dir.exists():
        return executions

    for pipeline_dir in summaries_dir.iterdir():
        if not pipeline_dir.is_dir():
            continue
        for exec_dir in pipeline_dir.iterdir():
            if not exec_dir.is_dir():
                continue
            events_file = exec_dir / "events.jsonl"
            if not events_file.exists():
                continue
            try:
                executions.append(
                    _build_execution_list_item(exec_dir, pipeline_dir.name)
                )
            except (json.JSONDecodeError, OSError) as e:  # Or more specific exceptions
                logger.error("Failed to aggregate %s: %s", exec_dir, e)
            except Exception as e:
                logger.critical(
                    "Unexpected error aggregating %s: %s", exec_dir, e, exc_info=True
                )
                # Depending on desired behavior, could re-raise or continue

    # Sort all executions chronologically (newest first) across all pipelines
    executions.sort(
        key=lambda e: e.get("started_at_utc") or e["timestamp"],
        reverse=True,
    )
    return executions


_STALE_THRESHOLD_SECONDS = 5 * 60  # No new events for 5 min → dead execution


def _build_execution_list_item(
    exec_dir: Path, pipeline_id_fallback: str
) -> dict[str, Any]:
    """Build lightweight metadata for execution cards.

    This intentionally avoids full execution aggregation so frequent list polling
    does not deserialize large event payloads for every execution.
    """
    first = _read_first_event(exec_dir / "events.jsonl")

    dir_name = exec_dir.name
    parts = dir_name.split("_", maxsplit=2)
    timestamp = "_".join(parts[:2]) if len(parts) >= 2 else dir_name
    execution_suffix = parts[2] if len(parts) >= 3 else dir_name

    pipeline_id = first.get("pipeline_id") or pipeline_id_fallback
    execution_id = first.get("execution_id") or execution_suffix
    started_at_utc = first.get("wall_clock") or _dir_timestamp_to_utc(dir_name)
    question = _to_question_preview(first.get("source_text", "Unknown question"))
    step_count = int(first.get("step_count", 0) or 0)

    return {
        "pipeline_id": pipeline_id,
        "execution_id": execution_id,
        "timestamp": timestamp,
        "started_at_utc": started_at_utc,
        "question": question,
        "step_count": step_count,
        "active_calls": [],
        "failed_calls": [],
        "summary": _build_summary([]),
        "is_live": not is_execution_complete(exec_dir),
    }


def _read_first_event(path: Path) -> dict[str, Any]:
    """Read only the first JSONL event from an execution file."""
    try:
        with path.open("r", encoding="utf-8") as fh:
            first_line = fh.readline().strip()
    except OSError:
        return {}

    if not first_line:
        return {}
    return json.loads(first_line)


def _to_question_preview(text: str) -> str:
    """Collapse whitespace and clamp very long source_text for list cards."""
    compact = " ".join(text.split())
    if len(compact) <= _LIST_QUESTION_PREVIEW_CHARS:
        return compact
    return f"{compact[:_LIST_QUESTION_PREVIEW_CHARS].rstrip()}..."


def is_execution_complete(exec_dir: Path) -> bool:
    """Check whether the execution has a terminal event or is stale.

    An execution is complete when:
    - Its last event is a terminal type (pipeline_completed/failed/cancelled), OR
    - Its events.jsonl hasn't been written to in _STALE_THRESHOLD_SECONDS
      (handles abrupt kills — e.g. Stargate restart — that never write a terminal event)

    Reads only the last portion of the file (tail) to avoid loading large logs.
    """
    events_file = exec_dir / "events.jsonl"
    if not events_file.exists():
        return False

    terminal_types = {"pipeline_completed", "pipeline_failed", "pipeline_cancelled"}
    chunk_size = 8192
    try:
        stat = events_file.stat()
        mtime = stat.st_mtime
        size = stat.st_size

        if size == 0:
            return False

        with events_file.open("rb") as f:
            read_size = min(size, chunk_size * 4)
            _ = f.seek(max(0, size - read_size), os.SEEK_SET)
            raw = f.read().decode("utf-8", errors="ignore")

        ev = _last_parseable_event(raw)
        if ev is None and size > read_size:
            # Fallback for giant single-line events: tail read can start mid-JSON.
            ev = _last_parseable_event(events_file.read_text(encoding="utf-8"))
        if ev is not None:
            if ev.get("event_type", "") in terminal_types:
                return True
            # Last parseable event is non-terminal — check staleness
            age_seconds = time.time() - mtime
            if age_seconds > _STALE_THRESHOLD_SECONDS:
                logger.info(
                    "Marking %s as complete: no terminal event and file "
                    "unmodified for %.0fs",
                    exec_dir.name,
                    age_seconds,
                )
                return True
            return False
    except OSError as e:
        logger.warning("Could not check completion for %s: %s", exec_dir, e)

    return False


# -- Internal helpers ---------------------------------------------------------


def _read_events(path: Path) -> list[dict[str, Any]]:
    """Read all events from a JSONL file."""
    events: list[dict[str, Any]] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError as e:
            logger.warning("Bad JSONL line in %s: %s", path, e)
    return events


def _last_parseable_event(raw: str) -> dict[str, Any] | None:
    """Return the last parseable JSON event from raw JSONL content."""
    for raw_line in reversed(raw.splitlines()):
        line = raw_line.strip()
        if not line:
            continue
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue
    return None


def _extract_question(events: list[dict[str, Any]]) -> str:
    """Extract the original question from PipelineStarted event."""
    for ev in events:
        if ev.get("event_type") == "pipeline_started":
            return ev.get("source_text", "Unknown question")
    return "Unknown question"


def _extract_wall_clock_ms(events: list[dict[str, Any]]) -> float | None:
    """Extract actual wall-clock duration from PipelineCompleted event."""
    for ev in events:
        if ev.get("event_type") == "pipeline_completed":
            return ev.get("duration_ms")
    return None


def _extract_started_at_utc(
    events: list[dict[str, Any]],
    exec_dir: Path,
) -> str | None:
    """Extract UTC wall clock for the pipeline start."""
    for ev in events:
        if ev.get("event_type") == "pipeline_started" and ev.get("wall_clock"):
            return ev["wall_clock"]
    if events:
        first_wall_clock = events[0].get("wall_clock")
        if first_wall_clock:
            return first_wall_clock
    return _dir_timestamp_to_utc(exec_dir.name)


def _dir_timestamp_to_utc(dir_name: str) -> str | None:
    """Convert ``YYYYMMDD_HHMMSS_*`` directory names to ISO UTC."""
    parts = dir_name.split("_", maxsplit=2)
    if len(parts) < 2:
        return None
    try:
        dt = datetime.strptime(f"{parts[0]}_{parts[1]}", "%Y%m%d_%H%M%S")
    except ValueError:
        return None
    return (
        dt.replace(tzinfo=UTC).isoformat()
    )  # Or keep .replace('+00:00', 'Z') with a comment if 'Z' is a strict requirement


def _create_default_step_data(step_id: str) -> dict[str, Any]:
    """Create default per-step state for event aggregation."""
    return {
        "step_id": step_id,
        "step_number": 0,
        "category": "",
        "status": "pending",
        "model": "",
        "model_ref": "",
        "model_calls": [],
        "active_model_call": None,
        "inference_ms": 0,
        "prompt_text_bytes": 0,
        "tokens": {"prompt": 0, "completion": 0, "total": 0},
        "iterations": [],
        "json_data": None,
        "domain_routing": None,
        "error": None,
    }


def _build_steps(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build ordered step list from events."""
    # Collect step data keyed by step_name
    step_data: dict[str, dict[str, Any]] = {}
    step_order: list[str] = []

    for ev in events:
        etype = ev.get("event_type", "")
        sname = ev.get("step_name", "")
        if not sname:
            continue

        if sname not in step_data:
            step_data[sname] = _create_default_step_data(sname)
            step_order.append(sname)

        sd = step_data[sname]
        apply_event(sd, ev, etype)

    # Build ordered list with step_number
    steps: list[dict[str, Any]] = []
    for i, sname in enumerate(step_order):
        sd = step_data[sname]
        sd["step_number"] = i + 1
        sd["category"] = _categorize_step(sd["step_id"])
        steps.append(sd)

    infer_verifier_pool(steps)
    return steps


def _extract_active_calls(steps: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Return currently running step/model pairs for list-card display."""
    active: list[dict[str, str]] = []
    for step in steps:
        if step.get("status") != "running":
            continue
        active_call = step.get("active_model_call") or {}
        latest_call = active_call.get("model", "")
        model_name = latest_call or step.get("model_ref") or step.get("model") or ""
        active.append(
            {
                "step_id": step.get("step_id", ""),
                "model": model_name,
            }
        )
    return active


def _extract_failed_calls(steps: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Return failed step/model pairs for list-card display."""
    failed: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for step in steps:
        step_id = step.get("step_id", "")
        step_had_failed_call = False
        for call in step.get("model_calls") or []:
            if call.get("success") is not False:
                continue
            model_name = (
                call.get("model") or step.get("model_ref") or step.get("model") or ""
            )
            if not model_name:
                continue
            key = (step_id, model_name)
            if key in seen:
                continue
            seen.add(key)
            failed.append({"step_id": step_id, "model": model_name})
            step_had_failed_call = True
        if step.get("status") == "failed" and not step_had_failed_call:
            model_name = step.get("model_ref") or step.get("model") or ""
            if not model_name:
                continue
            key = (step_id, model_name)
            if key in seen:
                continue
            seen.add(key)
            failed.append({"step_id": step_id, "model": model_name})
    return failed


def _categorize_step(step_id: str) -> str:
    """Assign a UI category to a step.

    Sub-pipeline steps (containing ``__``) are categorized by their
    parent prefix so expanded sub-steps inherit the parent's phase.
    """
    effective_step_id = step_id
    if "__" in step_id:
        parent = step_id.split("__", maxsplit=1)[0]
        if "verify" in parent or "veto" in parent:
            return "verify"
        if "synth" in parent:
            return "synthesize"
        effective_step_id = step_id.split("__", maxsplit=1)[1]

    if "analyze" in effective_step_id or "classify" in effective_step_id:
        return "classify"
    if "answer" in step_id or "reseed" in step_id:
        return "answer"
    if "verify" in step_id or "tiebreaker" in step_id:
        return "verify"
    if "enrich" in step_id:
        return "enrich"
    if "synth" in step_id or "post_process" in step_id:
        return "synthesize"
    if "output_gate" in step_id:
        return "gate"
    return "other"


def _build_summary(
    steps: list[dict[str, Any]],
    *,
    wall_clock_ms: float | None = None,
) -> dict[str, Any]:
    """Build aggregate summary statistics.

    Args:
        steps: Processed step dicts.
        wall_clock_ms: Actual elapsed time from PipelineCompleted event.
            Falls back to summed step latencies when unavailable (live streams).
    """
    total_prompt = 0
    total_completion = 0
    total_prompt_text_bytes = 0
    summed_latency = 0.0
    total_model_calls = 0
    total_claims = 0
    total_accepted = 0
    total_rejected = 0
    models_used: set[str] = set()

    for step in steps:
        tokens = step.get("tokens", {})
        total_prompt += tokens.get("prompt", 0)
        total_completion += tokens.get("completion", 0)
        total_prompt_text_bytes += step.get("prompt_text_bytes", 0)

        if step.get("latency_ms"):
            summed_latency += step["latency_ms"]

        if step.get("model"):
            models_used.add(step["model"])
        if step.get("iterations"):
            total_model_calls += len(step["iterations"])
            for it in step["iterations"]:
                if it.get("model"):
                    models_used.add(it["model"])

        json_data = step.get("json_data")
        if json_data:
            if "stats" in json_data:
                stats = json_data["stats"]
                total_claims += stats.get("total_claims", 0)
                total_accepted += stats.get("accepted", 0)
                total_rejected += stats.get("rejected", 0)
            elif "verified_facts" in json_data:
                n_v = len(json_data.get("verified_facts", []))
                n_r = len(json_data.get("rejected_claims", []))
                total_claims += n_v + n_r
                total_accepted += n_v
                total_rejected += n_r

        if json_data and "verdicts_by_model" in json_data:
            total_model_calls += len(json_data["verdicts_by_model"])

    return {
        "total_tokens": total_prompt + total_completion,
        "prompt_tokens": total_prompt,
        "completion_tokens": total_completion,
        "prompt_text_bytes": total_prompt_text_bytes,
        "wall_clock_ms": wall_clock_ms,
        "total_latency_ms": wall_clock_ms
        if wall_clock_ms is not None
        else summed_latency,
        "summed_latency_ms": summed_latency,
        "total_steps": len(steps),
        "models_used": sorted(models_used),
        "total_claims_verified": total_claims,
        "total_accepted": total_accepted,
        "total_rejected": total_rejected,
        "total_model_calls": total_model_calls,
    }


def _empty_execution(exec_dir: Path) -> dict[str, Any]:
    """Return an empty execution structure for directories without events."""
    dir_name = exec_dir.name
    parts = dir_name.split("_", maxsplit=2)
    return {
        "pipeline_id": exec_dir.parent.name,
        "execution_id": parts[2] if len(parts) >= 3 else dir_name,
        "timestamp": "_".join(parts[:2]) if len(parts) >= 2 else dir_name,
        "started_at_utc": _dir_timestamp_to_utc(dir_name),
        "question": "Unknown",
        "steps": [],
        "active_calls": [],
        "failed_calls": [],
        "summary": _build_summary([]),
    }
