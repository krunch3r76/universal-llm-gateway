"""Apply pipeline events to step data dicts.

Extracted from aggregator.py to keep both modules under SLOC limits.
Handles event dispatching, verifier pool inference, and convention-based
enrichment from StepOutput.json fields.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def apply_event(sd: dict[str, Any], ev: dict[str, Any], etype: str) -> None:
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
            new_prompt = ev.get("prompt_tokens", 0)
            new_completion = ev.get("completion_tokens", 0)
            if new_prompt or new_completion:
                # For map steps, iterations already accumulated tokens; do not overwrite.
                if sd.get("iterations") is None:
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

            # Convention-based enrichment from well-known StepOutput.json fields
            jd = sd.get("json_data")
            if isinstance(jd, dict):
                _auto_enrich_from_json(sd, jd)

        case "step_completed":
            if ev.get("duration_ms"):
                sd["latency_ms"] = ev["duration_ms"]
            prompt_tokens = ev.get("prompt_tokens")
            if prompt_tokens is not None:
                if "tokens" not in sd:
                    sd["tokens"] = {"prompt": 0, "completion": 0, "total": 0}
                completion_tokens = ev.get("completion_tokens", 0)
                sd["tokens"]["prompt"] = prompt_tokens
                sd["tokens"]["completion"] = completion_tokens
                sd["tokens"]["total"] = prompt_tokens + completion_tokens

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
            sd["tokens"]["prompt"] += iter_prompt
            sd["tokens"]["completion"] += iter_completion
            sd["tokens"]["total"] = sd["tokens"]["prompt"] + sd["tokens"]["completion"]

        case "verification_complete":
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

        case "assess_loop_started":
            if sd.get("json_data") is None:
                sd["json_data"] = {}
            sd["json_data"]["assess_loop"] = {
                "max_iterations": ev.get("max_iterations", 0),
                "terminal_action": ev.get("terminal_action", ""),
                "action_names": ev.get("action_names"),
            }

        case "assess_loop_iteration_completed":
            if sd["iterations"] is None:
                sd["iterations"] = []
            iter_prompt = ev.get("iteration_prompt_tokens", 0)
            iter_completion = ev.get("iteration_completion_tokens", 0)
            assess_ms = ev.get("assess_latency_ms", 0)
            action_ms = ev.get("action_latency_ms", 0)
            sd["iterations"].append(
                {
                    "index": ev.get("iteration", 0),
                    "action": ev.get("action", ""),
                    "reason": ev.get("reason", ""),
                    "is_terminal": ev.get("is_terminal", False),
                    "model": ev.get("action_model_id", ""),
                    "assess_latency_ms": assess_ms,
                    "action_latency_ms": action_ms,
                    "latency_ms": assess_ms + action_ms,
                    "prompt_tokens": iter_prompt,
                    "completion_tokens": iter_completion,
                }
            )

        case "assess_loop_completed":
            if sd.get("json_data") is None:
                sd["json_data"] = {}
            sd["json_data"]["exit_reason"] = ev.get("exit_reason", "")
            sd["json_data"]["iterations_used"] = ev.get("iterations_used", 0)
            sd["json_data"]["total_model_calls"] = ev.get("total_model_calls", 0)

        case "model_verdict_cast":
            pass

        case "tiebreaker_triggered":
            if sd["json_data"] is None:
                sd["json_data"] = {}
            sd["json_data"]["tiebreaker_triggered"] = {
                "borderline_claim_ids": ev.get("borderline_claim_ids", []),
                "tiebreaker_model": ev.get("tiebreaker_model", ""),
                "total_claims": ev.get("total_claims", 0),
                "math_excluded": ev.get("math_excluded", 0),
            }

        case "threshold_applied":
            pass

        case "model_invocation":
            call_inference_ms = ev.get("inference_ms", 0.0)
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
                    "inference_ms": call_inference_ms,
                    "prompt_tokens": ev.get("prompt_tokens", 0),
                    "completion_tokens": ev.get("completion_tokens", 0),
                    "success": ev.get("success", True),
                    "wall_clock": ev.get("wall_clock", ""),
                    "metadata": ev.get("metadata"),
                }
            )
            sd["inference_ms"] = sd.get("inference_ms", 0) + call_inference_ms

        case "step_failed":
            sd["error"] = ev.get("error")
            sd["traceback"] = ev.get("traceback")
            sd["latency_ms"] = ev.get("duration_ms")


def infer_verifier_pool(steps: list[dict[str, Any]]) -> None:
    """Infer full verifier pool and originator for verify steps lacking them.

    Across all verify steps in an execution, each pool member is excluded
    (as originator) in exactly one step.  The union of all voted model IDs
    therefore reveals the full pool.  Per-step, the single missing member
    is the originator.  Mutates step dicts in-place.
    """
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
        if jd.get("verifier_pool"):
            continue

        voters = set(jd["verdicts_by_model"].keys())
        missing = global_pool - voters

        jd["verifier_pool"] = sorted_pool
        if len(missing) == 1:
            jd["originator"] = next(iter(missing))


def _auto_enrich_from_json(sd: dict[str, Any], jd: dict[str, Any]) -> None:
    """Convention-based enrichment: detect well-known fields in StepOutput.json.

    ∀ handler H: H returns well-known fields in StepOutput.json ⟹
    aggregator populates the same step data as if domain events fired.
    """
    # 1. Domain routing: authority_verdicts → sd["domain_routing"]
    if "authority_verdicts" in jd and sd.get("domain_routing") is None:
        sd["domain_routing"] = {
            "authority_verdicts": jd["authority_verdicts"],
            "claims_routed_to_general": [
                c.get("statement_id", "") for c in jd.get("claims_for_general", [])
            ],
        }

    # 2. Assess loop: history → sd["iterations"] (non-streaming path)
    if (
        "history" in jd
        and isinstance(jd["history"], list)
        and sd.get("iterations") is None
    ):
        sd["iterations"] = [
            {
                "index": h.get("iteration", i),
                "action": h.get("action", ""),
                "reason": h.get("reason", ""),
                "is_terminal": h.get("is_terminal", False),
                "model": h.get("model", ""),
                "assess_latency_ms": h.get("assess_latency_ms", 0),
                "action_latency_ms": h.get("action_latency_ms", 0),
                "latency_ms": h.get("assess_latency_ms", 0)
                + h.get("action_latency_ms", 0),
                "prompt_tokens": h.get("prompt_tokens", 0),
                "completion_tokens": h.get("completion_tokens", 0),
            }
            for i, h in enumerate(jd["history"])
        ]

    # 3. Auto-compute stats from verified_facts + rejected_claims
    if "verified_facts" in jd and "rejected_claims" in jd:
        if not jd.get("stats"):
            n_accepted = len(jd["verified_facts"])
            n_rejected = len(jd["rejected_claims"])
            jd["stats"] = {
                "total_claims": n_accepted + n_rejected,
                "accepted": n_accepted,
                "rejected": n_rejected,
            }
