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
from pathlib import Path
from typing import Any

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

    # Build steps from events
    steps = _build_steps(events)

    return {
        "pipeline_id": pipeline_id,
        "execution_id": execution_id,
        "timestamp": timestamp,
        "question": question,
        "steps": steps,
        "summary": _build_summary(steps),
    }


def list_executions(summaries_dir: Path) -> list[dict[str, Any]]:
    """List all available executions that have events.jsonl files.

    Each entry includes an ``is_live`` flag so the frontend knows whether
    to open an SSE stream or fetch the final aggregate.
    """
    summaries_dir = Path(summaries_dir)
    executions: list[dict[str, Any]] = []

    if not summaries_dir.exists():
        return executions

    for pipeline_dir in sorted(summaries_dir.iterdir()):
        if not pipeline_dir.is_dir():
            continue
        for exec_dir in sorted(pipeline_dir.iterdir(), reverse=True):
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

    return executions


def is_execution_complete(exec_dir: Path) -> bool:
    """Check whether the execution has a terminal event (completed/failed/cancelled).

    Reads only the last few lines of the file to avoid parsing the whole thing.
    """
    events_file = exec_dir / "events.jsonl"
    if not events_file.exists():
        return False

    terminal_types = {"pipeline_completed", "pipeline_failed", "pipeline_cancelled"}
    try:
        raw = events_file.read_text(encoding="utf-8")
        # Scan from end for efficiency — terminal event is always last
        for line in reversed(raw.splitlines()):
            line = line.strip()
            if not line:
                continue
            ev = json.loads(line)
            return ev.get("event_type", "") in terminal_types
    except (json.JSONDecodeError, OSError) as e:
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
        _apply_event(sd, ev, etype)

    # Build ordered list with step_number
    steps: list[dict[str, Any]] = []
    for i, sname in enumerate(step_order):
        sd = step_data[sname]
        sd["step_number"] = i + 1
        sd["category"] = _categorize_step(sd["step_id"])
        steps.append(sd)

    _infer_verifier_pool(steps)
    return steps


def _apply_event(sd: dict[str, Any], ev: dict[str, Any], etype: str) -> None:
    """Apply a single event to a step dict."""
    match etype:
        case "step_started":
            sd["step_type"] = ev.get("step_type")
            if ev.get("model_id"):
                sd["model"] = ev["model_id"]
                sd["model_ref"] = ev["model_id"]

        case "step_inputs_captured":
            sd["inputs"] = ev.get("inputs", {})

        case "step_output_captured":
            sd["raw_output"] = ev.get("raw")
            sd["json_data"] = ev.get("json_data")
            sd["latency_ms"] = ev.get("latency_ms")
            if ev.get("model_id"):
                sd["model"] = ev["model_id"]
            # Only overwrite tokens if event has non-zero values
            # (preserves accumulated map_iteration tokens for map steps)
            new_prompt = ev.get("prompt_tokens", 0)
            new_completion = ev.get("completion_tokens", 0)
            if new_prompt or new_completion:
                sd["tokens"] = {
                    "prompt": new_prompt,
                    "completion": new_completion,
                    "total": new_prompt + new_completion,
                }
            if ev.get("system_prompt"):
                sd["system_prompt"] = ev["system_prompt"]
            if ev.get("user_prompt"):
                sd["user_prompt"] = ev["user_prompt"]
            if ev.get("request_body"):
                sd["request_body"] = ev["request_body"]

        case "step_completed":
            # Duration from StepCompleted overrides if present
            if ev.get("duration_ms"):
                sd["latency_ms"] = ev["duration_ms"]
            # Token counts from completion event (may include aggregated map tokens)
            if ev.get("prompt_tokens"):
                sd["tokens"]["prompt"] = ev["prompt_tokens"]
                sd["tokens"]["completion"] = ev.get("completion_tokens", 0)
                sd["tokens"]["total"] = ev["prompt_tokens"] + ev.get(
                    "completion_tokens", 0
                )

        case "map_iteration_completed":
            if sd["iterations"] is None:
                sd["iterations"] = []
            iter_prompt = ev.get("prompt_tokens", 0)
            iter_completion = ev.get("completion_tokens", 0)
            sd["iterations"].append(
                {
                    "index": ev.get("iteration_index", 0),
                    "key": ev.get("iteration_key", ""),
                    "model": ev.get("model_id", ""),
                    "latency_ms": ev.get("duration_ms", 0),
                    "output": ev.get("output_text", ""),
                    "prompt_tokens": iter_prompt,
                    "completion_tokens": iter_completion,
                }
            )
            # Accumulate iteration tokens into step totals
            sd["tokens"]["prompt"] += iter_prompt
            sd["tokens"]["completion"] += iter_completion
            sd["tokens"]["total"] = sd["tokens"]["prompt"] + sd["tokens"]["completion"]

        case "verification_complete":
            # Merge verification data into json_data (same shape as StepOutput.json)
            prev_cd = (sd.get("json_data") or {}).get("compound_decomposition")
            sd["json_data"] = {
                "verified_facts": ev.get("verified_facts", []),
                "rejected_claims": ev.get("rejected_claims", []),
                "verdicts_by_model": ev.get("verdicts_by_model", {}),
                "verifier_pool": ev.get("verifier_pool", []),
                "originator": ev.get("originator", ""),
                "stats": ev.get("stats", {}),
                "answer_sentences": ev.get("answer_sentences", []),
            }
            if prev_cd is not None:
                sd["json_data"]["compound_decomposition"] = prev_cd

        case "domain_verification_completed":
            sd["domain_routing"] = {
                "authority_verdicts": ev.get("authority_verdicts", {}),
                "claims_routed_to_general": ev.get("claims_routed_to_general", []),
            }

        case "claims_extracted" | "claims_classified" | "claims_contextualized":
            # Store for enrichment but don't overwrite primary data
            pass

        case "compound_claims_decomposed":
            if sd["json_data"] is None:
                sd["json_data"] = {}
            sd["json_data"]["compound_decomposition"] = {
                "decomposed_count": ev.get("decomposed_count", 0),
                "total_sub_claims": ev.get("total_sub_claims", 0),
                "decompose_latency_ms": ev.get("decompose_latency_ms", 0.0),
                "details": ev.get("details", []),
            }

        case "domain_veto_completed":
            if sd["domain_routing"] is None:
                sd["domain_routing"] = {}
            vetos = sd["domain_routing"].setdefault("domain_veto", [])
            vetos.append(
                {
                    "domain": ev.get("domain", ""),
                    "specialist_model": ev.get("specialist_model", ""),
                    "candidates_checked": ev.get("candidates_checked", 0),
                    "vetoed_ids": ev.get("vetoed_ids", []),
                    "survived_ids": ev.get("survived_ids", []),
                    "verdicts": ev.get("verdicts", {}),
                    "latency_ms": ev.get("latency_ms", 0.0),
                }
            )

        case "model_verdict_cast":
            # Individual model verdicts — aggregated in verification_complete
            pass

        case "tiebreaker_triggered":
            # Store tiebreaker info for UI
            if sd["json_data"] is None:
                sd["json_data"] = {}
            sd["json_data"]["tiebreaker_triggered"] = {
                "borderline_claim_ids": ev.get("borderline_claim_ids", []),
                "tiebreaker_model": ev.get("tiebreaker_model", ""),
                "total_claims": ev.get("total_claims", 0),
                "math_excluded": ev.get("math_excluded", 0),
            }

        case "threshold_applied":
            pass  # Threshold results captured in verification_complete

        case "model_invocation":
            sd["model_calls"].append(
                {
                    "call_label": ev.get("call_label", ""),
                    "model": ev.get("model_id", ""),
                    "snapshot_request_id": ev.get("snapshot_request_id", ""),
                    "system_prompt": ev.get("system_prompt"),
                    "user_prompt": ev.get("user_prompt", ""),
                    "request_body": ev.get("request_body"),
                    "response_text": ev.get("response_text"),
                    "error": ev.get("error"),
                    "latency_ms": ev.get("latency_ms", 0),
                    "prompt_tokens": ev.get("prompt_tokens", 0),
                    "completion_tokens": ev.get("completion_tokens", 0),
                    "success": ev.get("success", True),
                    "wall_clock": ev.get("wall_clock", ""),
                }
            )

        case "step_failed":
            sd["error"] = ev.get("error")
            sd["traceback"] = ev.get("traceback")
            sd["latency_ms"] = ev.get("duration_ms")


def _infer_verifier_pool(steps: list[dict[str, Any]]) -> None:
    """Infer full verifier pool and originator for verify steps lacking them.

    Across all verify steps in an execution, each pool member is excluded
    (as originator) in exactly one step.  The union of all voted model IDs
    therefore reveals the full pool.  Per-step, the single missing member
    is the originator.  Mutates step dicts in-place.
    """
    # Collect all model IDs that voted in any verify step
    global_pool: set[str] = set()
    verify_steps: list[dict[str, Any]] = []
    for step in steps:
        jd = step.get("json_data")
        if not jd or "verdicts_by_model" not in jd:
            continue
        voters = set(jd["verdicts_by_model"].keys())
        global_pool |= voters
        verify_steps.append(step)

    if not global_pool:
        return

    sorted_pool = sorted(global_pool)

    for step in verify_steps:
        jd = step["json_data"]
        # Skip steps that already have explicit pool data
        if jd.get("verifier_pool"):
            continue

        voters = set(jd["verdicts_by_model"].keys())
        missing = global_pool - voters

        jd["verifier_pool"] = sorted_pool
        # Exactly 1 missing → that's the originator (excluded by exclude_self)
        if len(missing) == 1:
            jd["originator"] = next(iter(missing))


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
    if "post_process" in step_id:
        return "synthesize"
    if "output_gate" in step_id:
        return "gate"
    return "other"


def _build_summary(steps: list[dict[str, Any]]) -> dict[str, Any]:
    """Build aggregate summary statistics (matches parser.py shape)."""
    total_prompt = 0
    total_completion = 0
    total_latency = 0.0
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
            total_latency += step["latency_ms"]

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

        if json_data and "verdicts_by_model" in json_data:
            total_model_calls += len(json_data["verdicts_by_model"])

    return {
        "total_tokens": total_prompt + total_completion,
        "prompt_tokens": total_prompt,
        "completion_tokens": total_completion,
        "total_latency_ms": total_latency,
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
