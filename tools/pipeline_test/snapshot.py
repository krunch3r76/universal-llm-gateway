"""Snapshot service: parse events.jsonl, resolve inputs, save/load fixtures.

Handles the graph-based input resolution that recovers full (untruncated)
values by tracing InputBinding sources back to prior step outputs.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .models import ExecutionSnapshot, ModelCall, StepSnapshot

SUMMARIES_ROOT = Path("/tmp/logs/universal-stargate/pipeline_summaries")

_TRUNCATION_RE = re.compile(r"\.\.\. \(\d+ chars total\)$")

_BINDING_RE = re.compile(
    r"InputBinding\("
    r"namespace='(?P<ns>[^']+)',\s*"
    r"step_name=(?:None|'(?P<step>[^']*)'),\s*"
    r"field_path='(?P<path>[^']*)'"
    r"\)"
)


def list_executions(pipeline_id: str) -> list[dict[str, str]]:
    """List available execution directories for a pipeline."""
    pipeline_dir = SUMMARIES_ROOT / pipeline_id
    if not pipeline_dir.is_dir():
        return []
    results: list[dict[str, str]] = []
    for d in sorted(pipeline_dir.iterdir(), reverse=True):
        if d.is_dir() and (d / "events.jsonl").exists():
            parts = d.name.split("_", 2)
            results.append(
                {
                    "dir_name": d.name,
                    "path": str(d / "events.jsonl"),
                    "execution_id": parts[2] if len(parts) >= 3 else d.name,
                    "timestamp": parts[0] if parts else "",
                }
            )
    return results


def load_execution(events_path: Path | str) -> ExecutionSnapshot:
    """Parse events.jsonl into a full ExecutionSnapshot with resolved inputs."""
    events_path = Path(events_path)
    events = _read_events(events_path)
    return _build_snapshot(events, str(events_path.parent))


def save_fixture(snapshot: ExecutionSnapshot, path: Path | str) -> None:
    """Serialize snapshot to JSON fixture file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _snapshot_to_dict(snapshot)
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


def load_fixture(path: Path | str) -> ExecutionSnapshot:
    """Deserialize a JSON fixture back into an ExecutionSnapshot."""
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    return _dict_to_snapshot(data)


# ---------------------------------------------------------------------------
# Internal: event parsing
# ---------------------------------------------------------------------------


def _read_events(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events


def _build_snapshot(events: list[dict[str, Any]], source_dir: str) -> ExecutionSnapshot:
    pipeline_id = ""
    execution_id = ""
    wall_clock = ""
    source_text = ""
    total_duration_ms = 0.0

    step_order: list[str] = []
    steps: dict[str, StepSnapshot] = {}

    outputs_by_step: dict[str, dict[str, Any]] = {}

    for ev in events:
        etype = ev.get("event_type", "")
        step_name = ev.get("step_name", "")

        if etype == "pipeline_started":
            pipeline_id = ev.get("pipeline_id", "")
            execution_id = ev.get("execution_id", "")
            wall_clock = ev.get("wall_clock", "")
            source_text = ev.get("source_text", "")

        elif etype == "pipeline_completed":
            total_duration_ms = ev.get("duration_ms", 0.0)

        elif etype == "step_started":
            if step_name not in steps:
                steps[step_name] = StepSnapshot(
                    step_name=step_name,
                    step_type=ev.get("step_type", ""),
                    model_id=ev.get("model_id"),
                )
                step_order.append(step_name)

        elif etype == "step_skipped":
            if step_name not in steps:
                steps[step_name] = StepSnapshot(
                    step_name=step_name,
                    step_type="",
                    skipped=True,
                    skip_reason=ev.get("reason", ""),
                )
                step_order.append(step_name)

        elif etype == "step_inputs_captured":
            step = steps.get(step_name)
            if step:
                raw_inputs = ev.get("inputs", {})
                step.input_sources = {
                    k: v.get("source", "") for k, v in raw_inputs.items()
                }
                step.inputs = _resolve_inputs(raw_inputs, outputs_by_step)

        elif etype == "step_output_captured":
            step = steps.get(step_name)
            if step:
                step.raw_output = ev.get("raw", "")
                step.json_output = ev.get("json_data")
                step.prompt_tokens = ev.get("prompt_tokens", 0)
                step.completion_tokens = ev.get("completion_tokens", 0)
                step.model_call_count = ev.get("model_call_count", 0)
            outputs_by_step[step_name] = {
                "raw": ev.get("raw", ""),
                "json": ev.get("json_data"),
            }

        elif etype == "step_completed":
            step = steps.get(step_name)
            if step:
                step.duration_ms = ev.get("duration_ms", 0.0)
                if not step.prompt_tokens:
                    step.prompt_tokens = ev.get("prompt_tokens", 0)
                if not step.completion_tokens:
                    step.completion_tokens = ev.get("completion_tokens", 0)
                if not step.model_call_count:
                    step.model_call_count = ev.get("model_call_count", 0)

        elif etype == "model_invocation":
            step = steps.get(step_name)
            if step:
                call = ModelCall(
                    call_label=ev.get("call_label", ""),
                    model_id=ev.get("model_id", ""),
                    system_prompt=ev.get("system_prompt", ""),
                    user_prompt=ev.get("user_prompt", ""),
                    request_body=ev.get("request_body", {}),
                    response_text=ev.get("response_text", ""),
                    prompt_tokens=ev.get("prompt_tokens", 0),
                    completion_tokens=ev.get("completion_tokens", 0),
                    latency_ms=ev.get("latency_ms", 0.0),
                    inference_ms=ev.get("inference_ms", 0.0),
                )
                step.model_calls.append(call)

        elif etype == "assess_loop_started":
            step = steps.get(step_name)
            if step:
                step.loop_config = {
                    "max_iterations": ev.get("max_iterations"),
                    "terminal_action": ev.get("terminal_action"),
                    "action_names": ev.get("action_names"),
                }
                step.loop_iterations = []

        elif etype == "assess_loop_iteration_completed":
            step = steps.get(step_name)
            if step and step.loop_iterations is not None:
                step.loop_iterations.append(
                    {
                        "iteration": ev.get("iteration"),
                        "decision": ev.get("decision"),
                    }
                )

    return ExecutionSnapshot(
        pipeline_id=pipeline_id,
        execution_id=execution_id,
        source_dir=source_dir,
        wall_clock=wall_clock,
        source_text=source_text,
        steps=steps,
        step_order=step_order,
        total_duration_ms=total_duration_ms,
    )


# ---------------------------------------------------------------------------
# Internal: input resolution (graph-based, no truncation)
# ---------------------------------------------------------------------------


def _resolve_inputs(
    raw_inputs: dict[str, dict[str, Any]],
    outputs_by_step: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Resolve step inputs, recovering full values for truncated strings."""
    resolved: dict[str, Any] = {}
    for input_name, binding_data in raw_inputs.items():
        value = binding_data.get("value")
        source = binding_data.get("source", "")

        if _is_truncated(value):
            full_value = _resolve_from_source(source, outputs_by_step)
            resolved[input_name] = full_value if full_value is not None else value
        else:
            resolved[input_name] = value
    return resolved


def _is_truncated(value: Any) -> bool:
    return isinstance(value, str) and bool(_TRUNCATION_RE.search(value))


def _resolve_from_source(
    source: str, outputs_by_step: dict[str, dict[str, Any]]
) -> Any | None:
    """Parse an InputBinding source and resolve the full value from prior step outputs."""
    m = _BINDING_RE.match(source)
    if not m:
        return None

    ns = m.group("ns")
    step_name = m.group("step")
    field_path = m.group("path")

    if ns != "step" or not step_name:
        return None

    step_output = outputs_by_step.get(step_name)
    if not step_output:
        return None

    return _traverse_output(step_output, field_path)


def _traverse_output(step_output: dict[str, Any], field_path: str) -> Any | None:
    """Navigate a field path like 'raw', 'json.verified_facts', 'qwen.raw'."""
    parts = field_path.split(".")

    if len(parts) == 1:
        if parts[0] == "raw":
            return step_output.get("raw")
        if parts[0] == "json":
            return step_output.get("json")
        json_data = step_output.get("json")
        if isinstance(json_data, dict):
            return json_data.get(parts[0])
        return None

    first, rest = parts[0], ".".join(parts[1:])

    if first == "json":
        obj = step_output.get("json")
        if isinstance(obj, dict):
            return _traverse_dict(obj, rest)
        return None

    json_data = step_output.get("json")
    if isinstance(json_data, dict) and first in json_data:
        sub = json_data[first]
        if isinstance(sub, dict):
            return _traverse_dict(sub, rest)
        return sub

    if first == "raw" and rest == "":
        return step_output.get("raw")

    return None


def _traverse_dict(obj: dict[str, Any], dotpath: str) -> Any | None:
    """Walk a dotted path through nested dicts."""
    current: Any = obj
    for part in dotpath.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current


# ---------------------------------------------------------------------------
# Internal: serialization
# ---------------------------------------------------------------------------


def _snapshot_to_dict(snapshot: ExecutionSnapshot) -> dict[str, Any]:
    return asdict(snapshot)


def _dict_to_snapshot(data: dict[str, Any]) -> ExecutionSnapshot:
    steps_data = data.pop("steps", {})
    step_order = data.pop("step_order", [])
    steps: dict[str, StepSnapshot] = {}
    for sname, sdata in steps_data.items():
        calls_data = sdata.pop("model_calls", [])
        model_calls = [ModelCall(**c) for c in calls_data]
        steps[sname] = StepSnapshot(**sdata, model_calls=model_calls)
    return ExecutionSnapshot(**data, steps=steps, step_order=step_order)
