"""Obligation coverage gate, cost rollup, and finalize envelope assembly."""

from __future__ import annotations

from typing import Any

from ._obligation_coverage import actionable_obligations, merge_audit_coverage_map
from .finalize_support import (
    _GENERATE_STEPS,
    _REQUIRED_ASK_KINDS,
    _apply_architect_omit_with_reason,
    _model_of,
    _rates,
    _raw_step,
)


def _obligation_coverage_gate(
    selected_body: str,
    obligations: list[dict[str, Any]],
    *,
    selected: str,
    audit_map: list[dict[str, Any]] | None,
    revision_ran: bool,
    architect_brief: str = "",
    channel: str = "",
    pin_ledger: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Re-score obligations on the shipped body; return coverage_map and optional refusal."""
    actionable = actionable_obligations(obligations)
    if not actionable or not selected_body.strip():
        return [], None
    coverage_map = merge_audit_coverage_map(
        selected_body,
        obligations,
        audit_map,
        selected=selected,
        pin_ledger=pin_ledger,
    )
    coverage_map = _apply_architect_omit_with_reason(
        coverage_map, architect_brief, channel=channel
    )
    missing = [
        row
        for row in coverage_map
        if row.get("status") == "missing"
        and row.get("reply_mode") != "omit_with_reason"
    ]
    if not missing:
        return coverage_map, None
    required_missing = [
        row for row in missing if row.get("kind") in _REQUIRED_ASK_KINDS
    ]
    fail = bool(required_missing) or (revision_ran and len(missing) >= 2)
    if not fail:
        return coverage_map, None
    unresolved: list[dict[str, Any]] = []
    for row in missing:
        oid = row.get("id")
        unresolved.append(
            {
                "type": "obligation_coverage_failed",
                "claim_id": oid,
                "detail": (
                    f"{oid} ({row.get('kind')}) remains missing: "
                    f"{row.get('obligation')}"
                ),
            }
        )
    return coverage_map, {
        "refused": "obligation_coverage_failed",
        "status": "refused",
        "unresolved": unresolved,
    }


def _cost(
    outputs: dict[str, Any],
    usage: dict[str, dict[str, int] | None],
    models: dict[str, str],
) -> tuple[float | None, str | None]:
    table, aliases = _rates()
    total = 0.0
    for step_name, role in _GENERATE_STEPS:
        spent = usage.get(step_name)
        if not spent:
            continue
        model = _model_of(_raw_step(outputs, step_name)) or models.get(role) or ""
        resolved = aliases.get(model) or model
        rate = table.get(resolved)
        if rate is None:
            rate = table.get(model)
        if rate is None:
            return None, f"rate_missing:{model}"
        prompt_rate, completion_rate = rate
        total += (spent["prompt_tokens"] * prompt_rate) / 1_000_000
        total += (spent["completion_tokens"] * completion_rate) / 1_000_000
    return total, None


def build_finalize_payload(
    *,
    coverage_map: list[dict[str, Any]],
    draft: Any,
    initial: list[Any],
    final: list[Any] | None,
    dropped_facts: list[Any],
    added_facts: list[Any],
    drift_check: dict[str, Any],
    review: dict[str, Any] | None,
    held_review: bool,
    review_ok: bool,
    review_reason: str | None,
    assemble: dict[str, Any],
    live_independence: str | None,
    revision: dict[str, Any] | None,
    passed: bool,
    unresolved: list[Any],
    selected: str,
    rhythm_reverted: bool,
    outputs: dict[str, Any],
    models: dict[str, str],
    revision_ran: bool,
    reviewer_live: str,
    usage: dict[str, dict[str, int] | None],
    est_cost: float | None,
    cost_note: str | None,
    filing_text: str | None,
    selected_body: str,
    obl_refusal: dict[str, Any] | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "coverage_map": coverage_map,
        "draft_v1": draft,
        "provenance": {
            "pin_ledger": assemble.get("pin_ledger"),
            "initial": initial,
            "final": final,
            "dropped_facts": dropped_facts,
            "added_facts": added_facts,
            "drift_check": drift_check,
        },
        "review": {
            "status": (
                "held_reviewer_unfunded"
                if held_review
                else ("ok" if review_ok else "failed")
            ),
            "reason": review_reason,
            "writer_seat": assemble.get("writer_seat"),
            "reviewer_seat": assemble.get("reviewer_seat"),
            "independence": live_independence,
            "findings": (review or {}).get("findings") if review_ok else None,
            "verdict": (review or {}).get("verdict") if review_ok else None,
            "rubric": assemble.get("check_class"),
        },
        "revision": revision,
        "ship_gate": {
            "pass": passed,
            "unresolved": unresolved,
            "selected": selected,
            "rhythm_reverted": rhythm_reverted,
        },
        "seats_used": {
            "writer": _model_of(_raw_step(outputs, "draft")) or models.get("writer"),
            "reviewer": reviewer_live or models.get("reviewer"),
            "reviser": (
                (_model_of(_raw_step(outputs, "revise")) or models.get("reviser"))
                if revision_ran
                else None
            ),
        },
        "usage": usage,
        "est_cost_usd": est_cost,
        "unsent": True,
        "seat_fallback": {
            "draft": draft.get("seat_fallback") if isinstance(draft, dict) else None,
            "review": (review or {}).get("seat_fallback")
            if isinstance(review, dict)
            else None,
        },
    }
    if cost_note:
        payload["cost_note"] = cost_note
    if filing_text is not None:
        payload["filing_text"] = filing_text
    payload["selected_draft"] = selected_body
    if obl_refusal is not None:
        payload["refused"] = obl_refusal["refused"]
        payload["ship_gate"]["pass"] = False
    return payload
