"""Build the UNSENT writer-specialist envelope from prior step outputs.

``WritingFinalizeHandler`` always runs. A refused assemble, a packet-only
run, or a failed independence check returns a short envelope. Otherwise the
envelope carries the draft, both provenance passes, the review, an optional
revision, and a ship gate. Nothing here sends or writes.
"""

from __future__ import annotations

from typing import Any

from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

from .email_envelope import has_email_subject
from .finalize_support import (
    _GENERATE_STEPS,
    _NUMBERED_PARAGRAPH,
    _apply_architect_omit_with_reason,  # noqa: F401 — re-export for tests
    _attach_status,
    _cost,
    _drift_failed,
    _json_of,
    _merge_drift,
    _model_of,
    _obligation_coverage_gate,
    _omit_with_reason_ids,  # noqa: F401 — re-export for draft_selector / tests
    _polish_email_draft_if_needed,
    _raw_step,
    _selected_body,
    _step,
    _step_ok,
    _stitch_filing,
    _usage_of,
    build_finalize_payload,
)
from .independence import (
    _allow_partial,
    is_independent,
    load_writing_models,
)


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
        if added_facts and not any(
            row.get("type") == "added_unattested_fact" for row in unresolved
        ):
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
            pin_ledger=list(assemble.get("pin_ledger") or []),
        )
        if obl_refusal is not None:
            passed = False
            unresolved.extend(obl_refusal["unresolved"])
        judge_fail = any(
            isinstance(row, dict) and row.get("type") == "semantic_judge_unavailable"
            for row in (initial + list(final or []))
        )
        if judge_fail:
            passed = False
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
        payload = build_finalize_payload(
            coverage_map=coverage_map,
            draft=draft,
            initial=initial,
            final=final,
            dropped_facts=dropped_facts,
            added_facts=added_facts,
            drift_check=drift_check,
            review=review,
            held_review=held_review,
            review_ok=review_ok,
            review_reason=review_reason,
            assemble=assemble,
            live_independence=live_independence,
            revision=revision,
            passed=passed,
            unresolved=unresolved,
            selected=selected,
            rhythm_reverted=rhythm_reverted,
            outputs=outputs,
            models=models,
            revision_ran=revision_ran,
            reviewer_live=reviewer_live,
            usage=usage,
            est_cost=est_cost,
            cost_note=cost_note,
            filing_text=filing_text,
            selected_body=selected_body,
            obl_refusal=obl_refusal,
        )
        return _step(_attach_status(payload))
