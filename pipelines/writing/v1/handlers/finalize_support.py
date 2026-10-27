"""Finalize helpers: usage, cost, omit-with-reason, obligation gate."""

from __future__ import annotations

import json
import re
from typing import Any

from systems.pipeline.core.handlers.protocol import StepOutput

from ._obligation_coverage import actionable_obligations, merge_audit_coverage_map
from .email_envelope import polish_email_draft

_REQUIRED_ASK_KINDS = frozenset({"question", "request", "call_to_action"})

_GENERATE_STEPS = (
    ("source_obligations", "prompt_architect"),
    ("prompt_architect", "prompt_architect"),
    ("drafter_openai", "drafter_openai"),
    ("drafter_google", "drafter_google"),
    ("drafter_xai", "drafter_xai"),
    ("drafter_local_hermes", "drafter_local_hermes"),
    ("drafter_local_ernie", "drafter_local_ernie"),
    ("drafter_anthropic", "drafter_anthropic"),
    ("obligation_coverage", "reviewer"),
    ("fact_ceiling_audit", "verifier"),
    ("prose_rhythm_pass", "reviser"),
)

def _step(payload: dict[str, Any]) -> StepOutput:
    return StepOutput(raw=json.dumps(payload), json=payload)


def _attach_status(payload: dict[str, Any]) -> dict[str, Any]:
    """Map refused/degraded runs to honest top-level status."""
    if payload.get("refused"):
        payload["status"] = "refused"
        if not payload.get("error"):
            payload["error"] = str(payload.get("refused"))
        return payload
    ship = payload.get("ship_gate") or {}
    review = payload.get("review") or {}
    degraded_reasons: list[str] = []
    if ship.get("pass") is False:
        unresolved = ship.get("unresolved") or []
        if unresolved and isinstance(unresolved[0], dict):
            degraded_reasons.append(
                str(unresolved[0].get("detail") or "ship_gate_unresolved")
            )
        else:
            degraded_reasons.append("ship_gate_failed")
    if review.get("status") == "failed":
        degraded_reasons.append(str(review.get("reason") or "review_failed"))
    if degraded_reasons:
        payload["status"] = "degraded"
        payload["error"] = "; ".join(degraded_reasons)
    elif "status" not in payload:
        payload["status"] = "complete"
    return payload


def _raw_step(outputs: dict[str, Any], name: str) -> Any:
    return outputs.get(name)


def _step_ok(step: Any) -> bool:
    if step is None:
        return False
    if isinstance(step, dict):
        return not step.get("error")
    return not getattr(step, "error", None)


_NUMBERED_PARAGRAPH = re.compile(r"(?m)^\d+\.\s")


def _drift_failed(row: Any) -> bool:
    return isinstance(row, dict) and row.get("passed") is False


def _merge_drift(v1: dict[str, Any], v2: dict[str, Any] | None) -> dict[str, Any]:
    """Envelope drift fails when the v1 check or the v2 check fails.

    Flag rows from both passes are kept. A missing v2 check does not fail
    the envelope. Callers use the merged ``passed`` bit for the ship gate
    so a clean v1 cannot hide a drifted rhythm draft.
    """
    flags = list(v1.get("flagged_sentences") or [])
    if isinstance(v2, dict):
        flags.extend(list(v2.get("flagged_sentences") or []))
    return {
        **v1,
        "passed": not _drift_failed(v1) and not _drift_failed(v2),
        "flagged_sentences": flags,
    }


def _polish_email_draft_if_needed(assemble: dict[str, Any], text: str) -> str:
    if str(assemble.get("channel") or "") != "email" or not text.strip():
        return text
    intake = assemble.get("intake")
    intake_dict = intake if isinstance(intake, dict) else {}
    recipient = str(intake_dict.get("recipient") or assemble.get("recipient") or "")
    signer = str(intake_dict.get("signer") or "")
    return polish_email_draft(
        text,
        recipient=recipient,
        signer=signer,
        intake=intake_dict,
        pin_ledger=list(assemble.get("pin_ledger") or []),
    )


def _selected_body(selected: str, draft: Any, revision: dict[str, Any] | None) -> str:
    if selected == "draft_v2" and isinstance(revision, dict):
        text = revision.get("draft_v2")
        if isinstance(text, str):
            return text
    if isinstance(draft, dict):
        inner = draft.get("draft")
        if isinstance(inner, str):
            return inner
    return ""


def _stitch_filing(frame: dict[str, Any], body: str) -> str:
    """Join source caption, opener, and jurat around the model body.

    Only non-empty source slices are copied. A missing caption or jurat is
    left out rather than replaced with a stock line. The body is the model
    text already checked for fact tokens.
    """
    parts: list[str] = []
    for key in ("caption", "opener"):
        piece = frame.get(key)
        if isinstance(piece, str) and piece.strip():
            parts.append(piece.strip("\n"))
    if body.strip():
        parts.append(body.strip("\n"))
    jurat = frame.get("jurat")
    if isinstance(jurat, str) and jurat.strip():
        parts.append(jurat.strip("\n"))
    return "\n\n".join(parts)


def _json_of(outputs: dict[str, Any], name: str) -> dict[str, Any] | None:
    step = _raw_step(outputs, name)
    if not _step_ok(step):
        return None
    data = step.get("json") if isinstance(step, dict) else getattr(step, "json", None)
    if isinstance(data, dict) and data.get("_skipped") is True:
        return None
    return data if isinstance(data, dict) else None


def _usage_of(step: Any) -> dict[str, int] | None:
    if not _step_ok(step):
        return None
    prompt = (
        getattr(step, "prompt_tokens", None)
        if not isinstance(step, dict)
        else step.get("prompt_tokens")
    )
    completion = (
        getattr(step, "completion_tokens", None)
        if not isinstance(step, dict)
        else step.get("completion_tokens")
    )
    calls = (
        getattr(step, "model_call_count", 0)
        if not isinstance(step, dict)
        else step.get("model_call_count", 0)
    ) or 0
    if prompt is None and completion is None and calls == 0:
        return None
    return {
        "prompt_tokens": int(prompt or 0),
        "completion_tokens": int(completion or 0),
    }


def _model_of(step: Any) -> str | None:
    if step is None:
        return None
    if isinstance(step, dict):
        model = step.get("model_id")
    else:
        model = getattr(step, "model_id", None)
    return str(model) if model else None


def _rates() -> tuple[dict[str, tuple[float, float]], dict[str, str]]:
    from event_store.model_rate_table import load_manual_rows

    rows, aliases = load_manual_rows()
    table = {
        model_id: (row.input_rate_per_m, row.output_rate_per_m)
        for model_id, row in rows.items()
    }
    return table, aliases


def _violation_key(row: Any) -> tuple[tuple[str, str], ...] | None:
    if not isinstance(row, dict):
        return None
    return tuple(sorted((str(key), str(value)) for key, value in row.items()))


def _v2_worse(initial: list[Any], final: list[Any]) -> bool:
    """True only when final has more violations than initial.

    A longer list returns immediately, so the keyed branch runs only when
    the lists are the same length or final is shorter. Equal or fewer
    violations are not worse. A different set of the same size is not worse.
    Gate evaluation then stays on v2.
    """
    if len(final) > len(initial):
        return True
    initial_keys = [key for row in initial if (key := _violation_key(row))]
    final_keys = [key for row in final if (key := _violation_key(row))]
    bag = list(final_keys)
    contained = True
    for key in initial_keys:
        if key not in bag:
            contained = False
            break
        bag.remove(key)
    return contained and len(final_keys) > len(initial_keys)


_OMIT_WITH_REASON = re.compile(
    r"\b(O\d+)\s*(?:\([^)]*\))?\s*:\s*omit_with_reason\b",
    re.I,
)
_OMIT_WITH_REASON_PROXIMITY = re.compile(
    r"\b(O\d+)\b(?:(?!\bO\d+\b)[^\n]){0,80}\bomit_with_reason\b",
    re.I,
)


def _omit_with_reason_ids(architect_brief: str) -> set[str]:
    ids = {match.group(1).upper() for match in _OMIT_WITH_REASON.finditer(architect_brief)}
    ids |= {
        match.group(1).upper()
        for match in _OMIT_WITH_REASON_PROXIMITY.finditer(architect_brief)
    }
    return ids


def _apply_architect_omit_with_reason(
    coverage_map: list[dict[str, Any]],
    architect_brief: str,
    *,
    channel: str,
) -> list[dict[str, Any]]:
    """Mark architect-declared omissions as covered (declined_with_reason)."""
    omit_ids = _omit_with_reason_ids(architect_brief)
    if not omit_ids:
        return coverage_map
    updated: list[dict[str, Any]] = []
    for row in coverage_map:
        oid = str(row.get("id") or "").upper()
        if oid in omit_ids:
            updated.append(
                {
                    **row,
                    "status": "declined_with_reason",
                    "reply_mode": "omit_with_reason",
                    "covering_sentence": row.get("covering_sentence")
                    or "omit_with_reason per architect brief",
                }
            )
        else:
            updated.append(dict(row))
    return updated


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

