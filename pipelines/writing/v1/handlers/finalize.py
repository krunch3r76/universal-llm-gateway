"""Build the UNSENT writer-specialist envelope from prior step outputs.

``WritingFinalizeHandler`` always runs. A refused assemble, a packet-only
run, or a failed independence check returns a short envelope. Otherwise the
envelope carries the draft, both provenance passes, the review, an optional
revision, and a ship gate. Nothing here sends or writes.
"""

from __future__ import annotations

import json
import re
from typing import Any

from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

from ._obligation_coverage import (
    actionable_obligations,
    merge_audit_coverage_map,
)
from .email_envelope import has_email_subject, polish_email_draft
from .independence import (
    _allow_partial,
    is_independent,
    load_writing_models,
)

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
        text, recipient=recipient, signer=signer, intake=intake_dict
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
    r"\b(O\d+)\s*:\s*omit_with_reason\b",
    re.I,
)


def _omit_with_reason_ids(architect_brief: str) -> set[str]:
    return {match.group(1).upper() for match in _OMIT_WITH_REASON.finditer(architect_brief)}


def _apply_architect_omit_with_reason(
    coverage_map: list[dict[str, Any]],
    architect_brief: str,
    *,
    channel: str,
) -> list[dict[str, Any]]:
    """Mark architect-declared omissions as covered (declined_with_reason)."""
    omit_ids = _omit_with_reason_ids(architect_brief)
    if not omit_ids and channel != "filing":
        return coverage_map
    if channel == "filing" and "omit_with_reason" in architect_brief.lower():
        for match in re.finditer(r"\b(O\d+)\b", architect_brief):
            if "omit_with_reason" in architect_brief[match.start() : match.start() + 80].lower():
                omit_ids.add(match.group(1).upper())
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


class WritingFinalizeHandler(BaseHandler):
    """Fold step outputs into one UNSENT envelope with a ship gate.

    Missing steps and steps whose ``error`` is set count as not ok. Token
    cost uses ``config/model_rates.yaml`` via the manual rate loader. A model
    with no row nulls ``est_cost_usd`` and sets ``cost_note``.

    Review transport or model failure after draft yields an envelope with
    draft_v1, provenance, review.status=failed, a review_unresolved row in
    ship_gate.unresolved, no revision, and ship_gate.pass=false. Partial
    independence adds independence_partial and cannot pass. Only full can.
    """

    step_type = "writing_finalize_v1"

    async def execute(self, step: Any, context: Any) -> StepOutput:
        """Read ``context.outputs`` and return the envelope for this run.

        Assemble refusals and packet runs return before review fields are
        required. Every payload sets ``unsent`` true. The handler does not
        send, post, or write a draft.
        """
        del step
        outputs = getattr(context, "outputs", {}) or {}
        assemble = _json_of(outputs, "assemble")
        if assemble is None:
            return _step(
                _attach_status(
                    {
                        "unsent": True,
                        "refused": "assemble_missing",
                        "error": "assemble step not ok",
                    }
                )
            )
        if assemble.get("refused"):
            return _step(
                _attach_status(
                    {
                        "unsent": True,
                        "refused": assemble.get("refused"),
                        "error": assemble.get("error"),
                    }
                )
            )
        if assemble.get("output") == "packet":
            return _step(
                {
                    "unsent": True,
                    "output": "packet",
                    "packet": assemble.get("packet"),
                    "pin_ledger": assemble.get("pin_ledger"),
                }
            )
        models = load_writing_models()
        draft = _json_of(outputs, "draft")
        if isinstance(draft, dict) and isinstance(draft.get("draft"), str):
            draft = {
                **draft,
                "draft": _polish_email_draft_if_needed(assemble, draft["draft"]),
            }
        if isinstance(draft, dict) and draft.get("refused") == "writer_seat_failed":
            return _step(
                _attach_status(
                    {
                        "unsent": True,
                        "refused": "writer_seat_failed",
                        "seat_fallback": {"draft": draft.get("seat_fallback")},
                        "error": draft.get("error"),
                    }
                )
            )
        initial_payload = _json_of(outputs, "provenance_check")
        initial = list((initial_payload or {}).get("violations") or [])
        dropped_facts = list((initial_payload or {}).get("dropped_facts") or [])
        for row in (initial_payload or {}).get("purpose_drops") or []:
            if not isinstance(row, dict):
                continue
            dropped_facts.append(
                {
                    "claim": row.get("sentence") or row.get("claim") or "",
                    "reason": "not_purpose_relevant",
                    "provenance": row.get("provenance"),
                }
            )
        added_facts = list((initial_payload or {}).get("added_facts") or [])
        drift_check = (initial_payload or {}).get("drift_check") or {
            "passed": True,
            "flagged_sentences": [],
        }
        independence = _json_of(outputs, "independence")
        if independence is None or independence.get("refused"):
            return _step(
                _attach_status(
                    {
                        "unsent": True,
                        "refused": "independence_violation",
                        "draft_v1": draft,
                        "provenance": {"initial": initial},
                    }
                )
            )
        review = _json_of(outputs, "fact_ceiling_audit") or _json_of(outputs, "review")
        held_review = bool(review) and review.get("status") == "held_reviewer_unfunded"
        review_ok = bool(review) and review.get("verdict") in {"ship", "revise"}
        rhythm = _json_of(outputs, "prose_rhythm_pass")
        rhythm_raw = None
        rhythm_step = _raw_step(outputs, "prose_rhythm_pass")
        if rhythm_step is not None:
            rhythm_raw = (
                rhythm_step.get("raw")
                if isinstance(rhythm_step, dict)
                else getattr(rhythm_step, "raw", None)
            )
        rhythm_failed = rhythm_step is not None and not _step_ok(rhythm_step)
        revision_ran = (not rhythm_failed) and (rhythm is not None or bool(rhythm_raw))
        final_payload = _json_of(outputs, "fact_ceiling_final")
        final = (
            list(final_payload.get("violations") or [])
            if final_payload is not None
            else None
        )
        rhythm_reverted = False
        selected = "draft_v2" if revision_ran and final == [] else "draft_v1"
        if final and len(final) > 0:
            selected = "draft_v1"
            rhythm_reverted = True
        v2_drift = None
        if isinstance(final_payload, dict):
            raw_v2_drift = final_payload.get("drift_check")
            if isinstance(raw_v2_drift, dict):
                v2_drift = raw_v2_drift
        drift_check = _merge_drift(drift_check, v2_drift)
        if _drift_failed(v2_drift) and selected == "draft_v2":
            selected = "draft_v1"
            rhythm_reverted = True
        if held_review or not review_ok:
            passed = False
        elif selected == "draft_v2":
            passed = final == []
        elif review_ok and initial_payload is not None:
            passed = initial == [] and review.get("verdict") == "ship"
        else:
            passed = False
        active = initial if selected == "draft_v1" else list(final or [])
        unresolved = list(active)
        review_reason = None
        if held_review:
            review_reason = (review or {}).get("reason") or "held_reviewer_unfunded"
            unresolved.append(
                {
                    "type": "review_held_unfunded",
                    "claim_id": None,
                    "detail": review_reason,
                }
            )
        elif not review_ok:
            review_reason = (
                (review or {}).get("reason")
                or (review or {}).get("refused")
                or "review_failed"
            )
            unresolved.append(
                {
                    "type": "review_unresolved",
                    "claim_id": None,
                    "detail": review_reason,
                }
            )
        if rhythm_reverted:
            unresolved.extend(list(final or []))
        if not revision_ran and review_ok and review.get("verdict") == "revise":
            unresolved.extend(list(review.get("findings") or []))
        options = getattr(context, "options", {}) or {}
        writer_live = (
            _model_of(_raw_step(outputs, "draft")) or models.get("writer") or ""
        )
        reviewer_live = ""
        if isinstance(review, dict) and review.get("reviewer_model"):
            reviewer_live = str(review["reviewer_model"])
        if not reviewer_live:
            audit_json = _json_of(outputs, "fact_ceiling_audit") or {}
            if isinstance(audit_json, dict) and audit_json.get("reviewer_model"):
                reviewer_live = str(audit_json["reviewer_model"])
        if not reviewer_live:
            reviewer_live = (
                _model_of(_raw_step(outputs, "fact_ceiling_audit"))
                or _model_of(_raw_step(outputs, "review"))
                or models.get("verifier")
                or models.get("reviewer")
                or ""
            )
        if is_independent(str(writer_live), str(reviewer_live)):
            live_independence: str | None = "full"
        elif _allow_partial(options):
            live_independence = "partial"
        else:
            live_independence = None
        if live_independence == "partial":
            passed = False
            unresolved.append(
                {
                    "type": "independence_partial",
                    "claim_id": None,
                    "detail": f"{writer_live}->{reviewer_live}",
                }
            )
        elif live_independence is None and not _allow_partial(options):
            passed = False
            unresolved.append(
                {
                    "type": "independence_mismatch",
                    "claim_id": None,
                    "detail": f"{writer_live}->{reviewer_live}",
                }
            )
        if added_facts:
            passed = False
            unresolved.append(
                {
                    "type": "added_unattested_fact",
                    "claim_id": None,
                    "detail": added_facts[0],
                }
            )
        if not drift_check.get("passed", True):
            passed = False
            flagged = drift_check.get("flagged_sentences") or []
            detail = (
                flagged[0].get("reason")
                if flagged and isinstance(flagged[0], dict)
                else "drift_check_failed"
            )
            unresolved.append(
                {
                    "type": "purpose_drift",
                    "claim_id": None,
                    "detail": str(detail),
                }
            )
        revision = None
        if revision_ran:
            draft_v2_text = rhythm_raw if isinstance(rhythm_raw, str) else None
            if draft_v2_text is None and isinstance(rhythm, dict):
                draft_v2_text = rhythm.get("draft")
            if isinstance(draft_v2_text, str):
                draft_v2_text = _polish_email_draft_if_needed(assemble, draft_v2_text)
            revision = {
                "draft_v2": draft_v2_text,
                "changed_claims": [],
                "dispositions": [],
                "rhythm_reverted": rhythm_reverted,
            }
        filing_text = None
        if str(assemble.get("channel") or "") == "filing":
            frame = assemble.get("filing_frame")
            if not isinstance(frame, dict):
                frame = {}
            body = _selected_body(selected, draft, revision)
            filing_text = _stitch_filing(frame, body)
            caption = str(frame.get("caption") or "").strip()
            jurat = str(frame.get("jurat") or "").strip()
            if caption and jurat and _NUMBERED_PARAGRAPH.search(body) is None:
                passed = False
                unresolved.append(
                    {
                        "type": "filing_shape",
                        "claim_id": None,
                        "detail": "numbered_paragraphs_required",
                    }
                )
        selected_body = _selected_body(selected, draft, revision)
        src_json = _json_of(outputs, "source_obligations") or {}
        obligations = list(src_json.get("obligations") or [])
        architect_json = _json_of(outputs, "prompt_architect") or {}
        architect_brief = str(architect_json.get("architect_brief") or "")
        channel = str(assemble.get("channel") or "")
        if not channel:
            intake = assemble.get("intake")
            if isinstance(intake, dict):
                channel = str(intake.get("channel") or "")
        obl_audit = _json_of(outputs, "obligation_coverage") or {}
        audit_map = list(obl_audit.get("coverage_map") or [])
        coverage_map, obl_refusal = _obligation_coverage_gate(
            selected_body,
            obligations,
            selected=selected,
            audit_map=audit_map or None,
            revision_ran=revision_ran,
            architect_brief=architect_brief,
            channel=channel,
        )
        if obl_refusal is not None:
            passed = False
            unresolved.extend(obl_refusal["unresolved"])
        if str(assemble.get("channel") or "") == "email" and not has_email_subject(
            selected_body
        ):
            passed = False
            unresolved.append(
                {
                    "type": "email_subject_missing",
                    "claim_id": None,
                    "detail": (
                        "Email draft lacks a Subject: line and intake did not "
                        "provide sufficient subject fields."
                    ),
                }
            )
        usage = {
            name: _usage_of(_raw_step(outputs, name)) for name, _role in _GENERATE_STEPS
        }
        est_cost, cost_note = _cost(outputs, usage, models)
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
                "findings": review.get("findings") if review_ok else None,
                "verdict": review.get("verdict") if review_ok else None,
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
                "writer": _model_of(_raw_step(outputs, "draft"))
                or models.get("writer"),
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
                "draft": draft.get("seat_fallback")
                if isinstance(draft, dict)
                else None,
                "review": review.get("seat_fallback")
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
        return _step(_attach_status(payload))
