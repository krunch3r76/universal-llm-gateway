"""
Verification report builder for consensus_verify_chain_v4 pipeline steps.

Outputs a machine-parseable verification_report.json next to summary.json
in each pipeline execution directory. Scripts can parse it to:

1. List per-model verdicts for each claim.
2. Find claims rejected by consensus (caught).
3. Find claims a given model voted true for that were rejected
   (that model "hallucinated" that claim).

Schema: See build_verification_report() return structure.
"""

from __future__ import annotations

from typing import Any

# Step type and JSON keys from consensus_verify_chain_v4 handler output
STEP_TYPE_VERIFY_CHAIN_V4 = "consensus_verify_chain_v4"
KEY_VERIFIED_FACTS = "verified_facts"
KEY_REJECTED_CLAIMS = "rejected_claims"
KEY_VERDICTS_BY_MODEL = "verdicts_by_model"
KEY_STATS = "stats"


def _infer_pass(step_id: str) -> int:
    """Pass 2 if step_id contains 'iter2'; else pass 1."""
    return 2 if "iter2" in step_id else 1


def _step_id_to_link(step_id: str) -> str:
    """
    Extract link name from step_id.

    verify_link0 → link0, verify_link1 → link1, verify_link0_iter2 → link0,
    verify_link1_iter2 → link1, etc.
    """
    if "iter2" in step_id:
        base = step_id.replace("_iter2", "")
    else:
        base = step_id
    if base.startswith("verify_"):
        return base[7:]  # "link0", "link1", ...
    return step_id


def _build_step_entry(step: dict[str, Any]) -> dict[str, Any] | None:
    """
    Build a single verification step entry from execution step.

    Returns None if step lacks required verification data.
    """
    step_id = step.get("step_id", "")
    j = step.get("json") or {}
    verified = j.get(KEY_VERIFIED_FACTS)
    rejected = j.get(KEY_REJECTED_CLAIMS)
    by_model = j.get(KEY_VERDICTS_BY_MODEL)
    stats = j.get(KEY_STATS)

    if not isinstance(by_model, dict):
        return None

    accepted_list = []
    if isinstance(verified, list):
        for item in verified:
            if isinstance(item, dict) and "statement_id" in item:
                accepted_list.append(
                    {"statement_id": item["statement_id"], "text": item.get("text", "")}
                )

    rejected_list = []
    if isinstance(rejected, list):
        for item in rejected:
            if isinstance(item, dict) and "statement_id" in item:
                rejected_list.append(
                    {"statement_id": item["statement_id"], "text": item.get("text", "")}
                )

    votes: dict[str, dict[str, bool]] = {}
    for model_id, verdicts in by_model.items():
        if isinstance(verdicts, dict):
            votes[str(model_id)] = {str(sid): bool(v) for sid, v in verdicts.items()}

    step_stats: dict[str, Any] = {}
    if isinstance(stats, dict):
        total = stats.get("total_claims") or stats.get("total") or 0
        acc = stats.get("accepted", 0)
        rej = stats.get("rejected", 0)
        step_stats = {"total": total, "accepted": acc, "rejected": rej}

    return {
        "step_id": step_id,
        "link": _step_id_to_link(step_id),
        "consensus": {
            "accepted": accepted_list,
            "rejected": rejected_list,
        },
        "votes_by_model": votes,
        "stats": step_stats,
    }


def build_verification_report(
    execution_details: dict[str, Any],
    metadata: dict[str, Any],
) -> dict[str, Any]:
    """
    Build verification report from execution details and metadata.

    Pure function: no I/O. Input source is same as summary.json:
    execution.steps[] where step_type == consensus_verify_chain_v4,
    with step.json containing verified_facts, rejected_claims,
    verdicts_by_model, stats.

    Args:
        execution_details: execution dict from summary (steps, step_count, etc.)
        metadata: pipeline_id, execution_id, timestamp_iso, source_text,
                  question (optional, falls back to source_text)

    Returns:
        Report dict suitable for JSON serialization.
    """
    steps = execution_details.get("steps") or []
    verify_steps = [s for s in steps if s.get("step_type") == STEP_TYPE_VERIFY_CHAIN_V4]

    if not verify_steps:
        return {
            "pipeline_id": metadata.get("pipeline_id", ""),
            "execution_id": metadata.get("execution_id", ""),
            "timestamp_iso": metadata.get("timestamp_iso", ""),
            "question": metadata.get("question") or metadata.get("source_text", ""),
            "passes": [],
            "aggregate": {
                "steps_with_verification": [],
                "all_claim_ids_by_step": {},
            },
        }

    passes_map: dict[int, list[dict[str, Any]]] = {}
    for step in verify_steps:
        entry = _build_step_entry(step)
        if entry is None:
            continue
        p = _infer_pass(entry["step_id"])
        passes_map.setdefault(p, []).append(entry)

    passes_list = [
        {"pass": p, "steps": sorted(steps_list, key=lambda x: x["step_id"])}
        for p, steps_list in sorted(passes_map.items())
    ]

    all_claim_ids: dict[str, list[str]] = {}
    for entry in (e for steps in passes_map.values() for e in steps):
        sid = entry["step_id"]
        ids_list: list[str] = []
        for acc in entry["consensus"]["accepted"]:
            ids_list.append(acc["statement_id"])
        for rej in entry["consensus"]["rejected"]:
            ids_list.append(rej["statement_id"])
        all_claim_ids[sid] = ids_list

    return {
        "pipeline_id": metadata.get("pipeline_id", ""),
        "execution_id": metadata.get("execution_id", ""),
        "timestamp_iso": metadata.get("timestamp_iso", ""),
        "question": metadata.get("question") or metadata.get("source_text", ""),
        "passes": passes_list,
        "aggregate": {
            "steps_with_verification": sorted(all_claim_ids.keys()),
            "all_claim_ids_by_step": all_claim_ids,
        },
    }
