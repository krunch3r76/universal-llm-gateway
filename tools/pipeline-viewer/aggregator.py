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
from pathlib import Path
from typing import Any

from event_apply import apply_event, infer_verifier_pool

logger = logging.getLogger(__name__)


def aggregate_execution(exec_dir: Path) -> dict[str, Any]:
    """Read events.jsonl and produce the viewer data contract.

    Returns dict with: pipeline_id, execution_id, timestamp, question,
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

    # Parse timestamp from directory name
    dir_name = exec_dir.name
    parts = dir_name.split("_", maxsplit=2)
    timestamp = f"{parts[0]}_{parts[1]}" if len(parts) >= 2 else dir_name

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
        "question": question,
        "steps": steps,
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
                data = aggregate_execution(exec_dir)
                executions.append(
                    {
                        "pipeline_id": data["pipeline_id"],
                        "execution_id": data["execution_id"],
                        "timestamp": data["timestamp"],
                        "question": data["question"],
                        "step_count": len(data["steps"]),
                        "summary": data["summary"],
                        "is_live": not is_execution_complete(exec_dir),
                    }
                )
            except Exception as e:
                logger.error("Failed to aggregate %s: %s", exec_dir, e)

    # Sort all executions chronologically (newest first) across all pipelines
    executions.sort(key=lambda e: e["timestamp"], reverse=True)
    return executions


def is_execution_complete(exec_dir: Path) -> bool:
    """Check whether the execution has a terminal event (completed/failed/cancelled).

    Reads only the last portion of the file (tail) to avoid loading large logs.
    """
    events_file = exec_dir / "events.jsonl"
    if not events_file.exists():
        return False

    terminal_types = {"pipeline_completed", "pipeline_failed", "pipeline_cancelled"}
    chunk_size = 8192
    try:
        with events_file.open("rb") as f:
            _ = f.seek(0, os.SEEK_END)
            size = f.tell()
            read_size = min(size, chunk_size * 4)
            if read_size == 0:
                return False
            _ = f.seek(max(0, size - read_size), os.SEEK_SET)
            raw = f.read().decode("utf-8", errors="ignore")
        for line in reversed(raw.splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
                return ev.get("event_type", "") in terminal_types
            except json.JSONDecodeError:
                continue
    except OSError as e:
        logger.warning("Could not check completion for %s: %s", exec_dir, e)

    return False


# -- Internal helpers ---------------------------------------------------------


def _read_events(path: Path) -> list[dict[str, Any]]:
    """Read all events from a JSONL file."""
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError as e:
            logger.warning("Bad JSONL line in %s: %s", path, e)
    return events


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
            step_data[sname] = {
                "step_id": sname,
                "step_type": None,
                "model": None,
                "model_ref": None,
                "latency_ms": None,
                "inference_ms": 0,
                "tokens": {"prompt": 0, "completion": 0, "total": 0},
                "inputs": {},
                "raw_output": None,
                "json_data": None,
                "iterations": None,
                "domain_routing": None,
                "model_calls": [],
                "error": None,
                "traceback": None,
            }
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


def _categorize_step(step_id: str) -> str:
    """Assign a UI category to a step.

    Sub-pipeline steps (containing ``__``) are categorized by their
    parent prefix so expanded sub-steps inherit the parent's phase.
    """
    if "__" in step_id:
        parent = step_id.split("__", maxsplit=1)[0]
        if "verify" in parent or "veto" in parent:
            return "verify"
        if "synth" in parent:
            return "synthesize"
        step_id = step_id.split("__", maxsplit=1)[1]

    if "analyze" in step_id or "classify" in step_id:
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
        if json_data and "stats" in json_data:
            stats = json_data["stats"]
            total_claims += stats.get("total_claims", 0)
            total_accepted += stats.get("accepted", 0)
            total_rejected += stats.get("rejected", 0)
        elif json_data and "verified_facts" in json_data:
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
        "timestamp": f"{parts[0]}_{parts[1]}" if len(parts) >= 2 else dir_name,
        "question": "Unknown",
        "steps": [],
        "summary": _build_summary([]),
    }
