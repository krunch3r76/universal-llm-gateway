"""Deterministic provenance and fact-ceiling checks for writer-specialist drafts."""

from __future__ import annotations

import json
from typing import Any

from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput
from writing_intake.claim_gates import check_class_for_channel
from writing_intake.fact_inventory import run_drift_check

from .email_envelope import (
    has_email_subject,
    polish_email_draft,
)
from .judge_provenance import (
    added_facts_from_judge_clauses,
    clause_violations,
    judge_unavailable_violation,
    verbatim_ledger_violations,
    violations_from_judge_pins,
    violations_from_quality_findings,
)
from .judge_winner_rows import winner_judge_rows

# Frozen regex symbols retained for spec compliance (used in provenance_revision).
from .provenance_draft import _NAME, _NUMBER, _parse_draft, _sentences  # noqa: F401
from .provenance_revision import revision_delta
from .purpose_scrub import (  # noqa: F401 — re-export for offline ship-gate fixtures
    _scrub_purpose_restatement_flags,
    scrub_purpose_restatement_flags,
    sentence_restates_purpose_point,
)


def _step(payload: dict[str, Any]) -> StepOutput:
    return StepOutput(raw=json.dumps(payload), json=payload)


def _violation(kind: str, claim_id: str | None, detail: Any) -> dict[str, Any]:
    return {"type": kind, "claim_id": claim_id, "detail": detail}


def check_fact_ceiling(
    pin_ledger: list[dict[str, Any]] | None,
    draft_text: str,
    *,
    max_chars: int | None = None,
) -> list[dict[str, Any]]:
    """Mechanical fact-ceiling checks retained after semantic_judge migration."""
    del pin_ledger
    violations: list[dict[str, Any]] = []
    if max_chars is not None and len(draft_text) > max_chars:
        violations.append(_violation("max_chars_exceeded", None, len(draft_text)))
    em_count = draft_text.count("\u2014")
    if em_count:
        violations.append(_violation("em_dash", None, em_count))
    return violations


def check_provenance(
    pin_ledger: list[dict[str, Any]] | None,
    draft_json: Any,
    judge_json: dict[str, Any] | None = None,
    *,
    winner_id: str | None = None,
    channel: str | None = None,
    signer: str | None = None,
) -> list[dict[str, Any]]:
    """Return provenance violations using semantic_judge rows when provided."""
    parsed = _parse_draft(draft_json)
    if parsed is None:
        return [_violation("draft_unparseable", None, "malformed")]
    ledger = pin_ledger or []
    judge = judge_json or {}
    if judge.get("judge_unavailable"):
        return [judge_unavailable_violation()]
    clauses = winner_judge_rows(list(judge.get("clauses") or []), winner_id)
    pins = winner_judge_rows(list(judge.get("pins") or []), winner_id)
    violations: list[dict[str, Any]] = []
    violations.extend(violations_from_judge_pins(ledger, pins, winner_id=winner_id))
    violations.extend(
        clause_violations(clauses, ledger, channel=channel, signer=signer)
    )
    quality = winner_judge_rows(
        list(judge.get("quality_findings") or []), winner_id
    )
    violations.extend(
        violations_from_quality_findings(quality, winner_id=winner_id)
    )
    violations.extend(check_fact_ceiling(ledger, parsed["draft"], max_chars=None))
    return violations


def _resolve_binding(step: Any, context: Any, field_name: str) -> Any:
    inputs = getattr(step, "handler_inputs", None) or {}
    binding = inputs.get(field_name)
    if binding is None:
        return None
    step_name = getattr(binding, "step_name", None)
    field_path = getattr(binding, "field_path", None)
    if isinstance(binding, str):
        if "." not in binding:
            return None
        step_name, field_path = binding.split(".", 1)
    outputs = getattr(context, "outputs", {}) or {}
    value: Any = outputs.get(step_name)
    if value is None or not field_path:
        return None
    for part in str(field_path).split("."):
        if value is None:
            return None
        if part == "json" and not isinstance(value, dict):
            value = getattr(value, "json", None)
            continue
        if isinstance(value, dict):
            value = value.get(part)
        else:
            value = getattr(value, part, None)
    return value


def _output_json(outputs: dict[str, Any], step_name: str) -> dict[str, Any] | None:
    row = outputs.get(step_name)
    if isinstance(row, dict):
        payload = row.get("json")
        return payload if isinstance(payload, dict) else None
    payload = getattr(row, "json", None)
    return payload if isinstance(payload, dict) else None


class WritingProvenanceCheckHandler(BaseHandler):
    """Pipeline step handler for provenance, fact-ceiling, and email polish checks."""

    step_type = "writing_provenance_check_v1"

    async def execute(self, step: Any, context: Any) -> StepOutput:
        ledger = _resolve_binding(step, context, "pin_ledger")
        draft = _resolve_binding(step, context, "draft")
        prior = _resolve_binding(step, context, "draft_v1")
        judge = _resolve_binding(step, context, "judge")
        if not isinstance(ledger, list):
            ledger = []
        judge_dict = judge if isinstance(judge, dict) else {}
        outputs = getattr(context, "outputs", {}) or {}
        assemble = outputs.get("assemble")
        assemble_json = (
            assemble.get("json")
            if isinstance(assemble, dict)
            else getattr(assemble, "json", None)
        )
        max_chars = None
        channel = None
        if isinstance(assemble_json, dict):
            max_chars = assemble_json.get("max_chars")
            channel = assemble_json.get("channel")
        draft_out = outputs.get("draft")
        winner_id = None
        if isinstance(draft_out, dict):
            winner_id = str(draft_out.get("json", {}).get("selected_step") or "")
        elif draft_out is not None:
            dj = getattr(draft_out, "json", None)
            if isinstance(dj, dict):
                winner_id = str(dj.get("selected_step") or "")

        intake_dict: dict[str, Any] = {}
        if isinstance(assemble_json, dict):
            intake_block = assemble_json.get("intake")
            if isinstance(intake_block, dict):
                intake_dict = intake_block
        signer = str(intake_dict.get("signer") or "")

        parsed = _parse_draft(draft)
        pre_polish_draft = (
            str(parsed.get("draft") or "") if parsed is not None else ""
        )
        if parsed is not None and str(channel or "") == "email":
            recipient = str(
                intake_dict.get("recipient")
                or (assemble_json or {}).get("recipient")
                or ""
            )
            parsed["draft"] = polish_email_draft(
                pre_polish_draft,
                recipient=recipient,
                signer=signer,
                intake=intake_dict,
                pin_ledger=ledger,
            )
        violations: list[dict[str, Any]] = []
        if parsed is None:
            violations = [_violation("draft_unparseable", None, "malformed")]
        elif judge_dict.get("judge_unavailable"):
            violations = [judge_unavailable_violation()]
        else:
            violations = check_provenance(
                ledger,
                parsed,
                judge_dict,
                winner_id=winner_id or None,
                channel=str(channel or "") or None,
                signer=signer or None,
            )
            if str(channel or "") == "email":
                draft_body = str(parsed.get("draft") or "")
                violations.extend(
                    verbatim_ledger_violations(
                        ledger, draft_body, pre_polish=pre_polish_draft
                    )
                )
                if not has_email_subject(draft_body):
                    violations.append(
                        _violation(
                            "email_subject_missing",
                            None,
                            "Email draft lacks a Subject: line and intake did not "
                            "provide sufficient subject fields.",
                        )
                    )
            if max_chars is not None:
                violations.extend(
                    check_fact_ceiling(
                        ledger,
                        parsed["draft"],
                        max_chars=max_chars,
                    )
                )
        if prior is not None and parsed is not None and not judge_dict.get(
            "judge_unavailable"
        ):
            v2_clauses = winner_judge_rows(
                list(judge_dict.get("clauses") or []), winner_id or None
            )
            v1_judge = _output_json(outputs, "semantic_judge_candidates") or {}
            v1_clauses = winner_judge_rows(
                list(v1_judge.get("clauses") or []), winner_id or None
            )
            prior_for_delta: Any = prior
            if str(channel or "") == "email":
                recipient = str(
                    intake_dict.get("recipient")
                    or (assemble_json or {}).get("recipient")
                    or ""
                )
                prior_parsed = _parse_draft(prior)
                if prior_parsed is not None:
                    prior_parsed = dict(prior_parsed)
                    prior_parsed["draft"] = polish_email_draft(
                        str(prior_parsed.get("draft") or ""),
                        recipient=recipient,
                        signer=signer,
                        intake=intake_dict,
                        pin_ledger=ledger,
                    )
                    prior_for_delta = prior_parsed
            violations.extend(
                revision_delta(
                    prior_for_delta,
                    parsed,
                    v1_clauses=v1_clauses,
                    v2_clauses=v2_clauses,
                    pin_ledger=ledger,
                    channel=channel,
                    signer=signer or None,
                )
            )
        dropped_facts: list[dict[str, str]] = []
        if isinstance(assemble_json, dict):
            raw_dropped = assemble_json.get("dropped_facts")
            if isinstance(raw_dropped, list):
                dropped_facts = [
                    row
                    for row in raw_dropped
                    if isinstance(row, dict) and row.get("claim") and row.get("reason")
                ]
        winner_clauses = winner_judge_rows(
            list(judge_dict.get("clauses") or []), winner_id or None
        )
        added_facts = added_facts_from_judge_clauses(
            winner_clauses,
            channel=str(channel or "") or None,
            signer=signer or None,
        )
        effective_channel = str(channel or "email")
        purpose_points: list[str] = []
        purpose_text = ""
        intake_answers: list[str] = []
        verbatim_notes: list[str] = []
        if isinstance(assemble_json, dict):
            raw_points = assemble_json.get("purpose_points")
            intake_block = assemble_json.get("intake") or {}
            if not isinstance(raw_points, list) and isinstance(intake_block, dict):
                raw_points = intake_block.get("purpose_points")
            if isinstance(raw_points, list):
                purpose_points = [str(item) for item in raw_points]
            if isinstance(intake_block, dict):
                purpose_text = str(
                    intake_block.get("purpose")
                    or intake_block.get("objective")
                    or assemble_json.get("objective")
                    or ""
                ).strip()
                raw_answers = intake_block.get("intake_answers")
                if isinstance(raw_answers, list):
                    intake_answers = [str(item) for item in raw_answers]
                raw_notes = intake_block.get("verbatim_notes")
                if isinstance(raw_notes, list):
                    verbatim_notes = [str(note) for note in raw_notes if str(note).strip()]
        load_bearing_claims = [
            str(i)
            for i in (assemble_json or {}).get("load_bearing_claims") or []
            if str(i).strip()
        ] or None
        drift: dict[str, Any] = {
            "passed": True,
            "flagged_sentences": [],
            "check_class": check_class_for_channel(effective_channel),
        }
        if parsed is not None:
            drift = run_drift_check(
                str(parsed.get("draft") or ""),
                channel=effective_channel,
                pin_ledger=ledger,
                purpose_points=purpose_points or None,
                purpose=purpose_text or None,
                intake_answers=intake_answers or None,
                load_bearing_claims=load_bearing_claims,
            )
            if purpose_points or purpose_text.strip():
                drift = scrub_purpose_restatement_flags(
                    drift,
                    list(purpose_points),
                    pin_ledger=ledger,
                    purpose_text=purpose_text,
                    verbatim_notes=verbatim_notes,
                )
        flagged = drift.get("flagged_sentences") or []
        purpose_drops = [
            {"sentence": row["sentence"], "reason": "not_purpose_relevant"}
            for row in flagged
            if isinstance(row, dict) and row.get("reason") == "not_purpose_relevant"
        ]
        if added_facts and not any(
            v.get("type") == "added_unattested_fact" for v in violations
        ):
            violations.append(
                _violation("added_unattested_fact", None, added_facts[0][:120])
            )
        if not drift.get("passed"):
            violations.append(_violation("purpose_drift", None, "drift_check_failed"))
        payload = {
            "ok": True,
            "violations": violations,
            "pass": violations == [],
            "dropped_facts": dropped_facts,
            "added_facts": added_facts,
            "drift_check": drift,
            "channel": effective_channel,
            "check_class": drift.get("check_class"),
            "purpose_drops": purpose_drops,
        }
        return _step(payload)
