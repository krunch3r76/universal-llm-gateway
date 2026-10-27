"""Deterministic provenance and fact-ceiling checks for writer-specialist drafts."""

from __future__ import annotations

import json
import re
from typing import Any

from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput
from writing_intake.claim_gates import check_class_for_channel
from writing_intake.fact_inventory import run_drift_check

from .email_envelope import (
    has_email_subject,
    normalize_contractions,
    polish_email_draft,
)
from .judge_provenance import (
    added_facts_from_judge_clauses,
    clause_violations,
    judge_unavailable_violation,
    verbatim_ledger_violations,
    violations_from_judge_pins,
)

_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_NAME = re.compile(r"\b[A-Z][a-z]{2,}\b")
_NUMBER = re.compile(r"\b\d+(?:\.\d+)?\b")
_NAME_STOP = frozenset(
    {
        "The",
        "This",
        "That",
        "Could",
        "Would",
        "Please",
        "Dear",
        "And",
        "But",
        "So",
        "It",
        "When",
        "Do",
        "Does",
        "Did",
        "Can",
        "Will",
        "Is",
        "Are",
        "Was",
        "Were",
        "Have",
        "Has",
        "Had",
        "Would",
        "Could",
        "Should",
        "Not",
        "Hi",
        "Hello",
    }
)
_ENVELOPE_OR_EXHIBIT_LINE_RE = re.compile(
    r"^\s*(?:(?:to|cc|bcc|from|subject|re|date)\s*:|Exhibit\s+\d+\s*:|"
    r"(?:thank you|thanks|sincerely|regards|best regards|kind regards|respectfully)[,!]?\s*$)",
    re.IGNORECASE,
)
_VALEDICTION_LINE_RE = re.compile(
    r"^\s*(?:thank you|thanks|sincerely|regards|best regards|kind regards|respectfully)[,!]?\s*$",
    re.IGNORECASE,
)
_TOKEN = re.compile(r"[a-z0-9]+")
_FUNCTION_WORDS = frozenset(
    {
        "also",
        "am",
        "and",
        "when",
        "the",
        "a",
        "it",
        "is",
        "was",
        "where",
        "of",
        "i",
        "its",
        "that",
        "in",
        "on",
        "so",
        "to",
        "hi",
        "hello",
    }
)


def _step(payload: dict[str, Any]) -> StepOutput:
    return StepOutput(raw=json.dumps(payload), json=payload)


def _violation(kind: str, claim_id: str | None, detail: Any) -> dict[str, Any]:
    return {"type": kind, "claim_id": claim_id, "detail": detail}


def _parse_draft(draft_json: Any) -> dict[str, Any] | None:
    if isinstance(draft_json, str):
        text = draft_json.strip()
        if not text:
            return None
        if text.startswith("{"):
            try:
                draft_json = json.loads(text)
            except json.JSONDecodeError:
                return {"draft": text, "claims": [], "need": [], "dispositions": []}
        else:
            return {"draft": text, "claims": [], "need": [], "dispositions": []}
    if not isinstance(draft_json, dict):
        return None
    if "draft" not in draft_json and isinstance(draft_json.get("text"), str):
        return {
            "draft": draft_json["text"],
            "claims": [],
            "need": [],
            "dispositions": [],
        }
    if "draft" not in draft_json:
        return None
    if isinstance(draft_json.get("draft"), str):
        out = dict(draft_json)
        out.setdefault("claims", [])
        return out
    return None


def _sentences(text: str) -> list[str]:
    blocks: list[str] = []
    current: list[str] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        raw_line = lines[i].strip()
        if not raw_line:
            if current:
                blocks.append(" ".join(current))
                current = []
            i += 1
            continue
        if _VALEDICTION_LINE_RE.match(raw_line):
            if current:
                blocks.append(" ".join(current))
                current = []
            closing = [raw_line]
            if i + 1 < len(lines) and lines[i + 1].strip():
                closing.append(lines[i + 1].strip())
                i += 1
            blocks.append("\n".join(closing))
            i += 1
            continue
        if _ENVELOPE_OR_EXHIBIT_LINE_RE.match(raw_line):
            if current:
                blocks.append(" ".join(current))
                current = []
            blocks.append(raw_line)
            i += 1
            continue
        current.append(raw_line)
        i += 1
    if current:
        blocks.append(" ".join(current))
    out: list[str] = []
    for block in blocks:
        if "\n" in block or _ENVELOPE_OR_EXHIBIT_LINE_RE.match(block):
            out.append(block.strip())
        else:
            for part in _SENTENCE.split(block):
                if part.strip():
                    out.append(part.strip())
    return out


def check_fact_ceiling(
    pin_ledger: list[dict[str, Any]] | None,
    draft_text: str,
    *,
    max_chars: int | None = None,
    channel: str | None = None,
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
) -> list[dict[str, Any]]:
    """Return provenance violations using semantic_judge rows when provided."""
    parsed = _parse_draft(draft_json)
    if parsed is None:
        return [_violation("draft_unparseable", None, "malformed")]
    ledger = pin_ledger or []
    judge = judge_json or {}
    if judge.get("judge_unavailable"):
        return [judge_unavailable_violation()]
    clauses = list(judge.get("clauses") or [])
    pins = list(judge.get("pins") or [])
    violations: list[dict[str, Any]] = []
    violations.extend(violations_from_judge_pins(ledger, pins, winner_id=winner_id))
    violations.extend(clause_violations(clauses, ledger))
    violations.extend(
        check_fact_ceiling(ledger, parsed["draft"], channel=None, max_chars=None)
    )
    return violations


def _pin_order(clauses: list[dict[str, Any]]) -> list[str]:
    order: list[str] = []
    for clause in clauses:
        pin_ids = clause.get("pin_ids") if isinstance(clause.get("pin_ids"), list) else []
        for pin_id in pin_ids:
            key = str(pin_id)
            if key not in order:
                order.append(key)
    return order


def revision_delta(
    draft_v1: Any,
    draft_v2: Any,
    *,
    v2_clauses: list[dict[str, Any]] | None = None,
    pin_ledger: list[dict[str, Any]] | None = None,
    channel: str | None = None,
    signer: str | None = None,
) -> list[dict[str, Any]]:
    """Literal dropped-name/number checks and fact_reordered from judge clause order."""
    del signer
    prior = _parse_draft(draft_v1)
    current = _parse_draft(draft_v2)
    if prior is None or current is None:
        return []
    v1_text = prior["draft"]
    v2_text = current["draft"]
    v1_name_text = normalize_contractions(v1_text)
    v2_name_text_lower = normalize_contractions(v2_text).lower()
    v2_clause_rows = v2_clauses or []
    violations: list[dict[str, Any]] = []
    v1_order = _pin_order(v2_clause_rows)
    v2_order = _pin_order(v2_clause_rows)
    if v1_order and v2_order and v1_order != v2_order:
        profile = check_class_for_channel(str(channel or "filing"))
        if profile == "sworn":
            violations.append(
                _violation("fact_reordered", None, {"v1": v1_order, "v2": v2_order})
            )
    for name in _NAME.findall(v1_name_text):
        if name in _NAME_STOP:
            continue
        if name.lower() not in v2_name_text_lower:
            violations.append(_violation("v2_dropped_name", None, name))
            violations.append(_violation("fact_dropped", None, name))
    for number in _NUMBER.findall(v1_text):
        if number not in v2_text:
            violations.append(_violation("v2_dropped_number", None, number))
            violations.append(_violation("fact_dropped", None, number))
    for row in v2_clause_rows:
        if not isinstance(row, dict):
            continue
        if row.get("attested") is False and str(row.get("text") or "").strip():
            violations.append(
                _violation(
                    "added_unattested_fact",
                    None,
                    str(row.get("text") or "")[:120],
                )
            )
    del pin_ledger
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
        assemble = getattr(context, "outputs", {}).get("assemble")
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
        outputs = getattr(context, "outputs", {}) or {}
        draft_out = outputs.get("draft")
        winner_id = None
        if isinstance(draft_out, dict):
            winner_id = str(draft_out.get("json", {}).get("selected_step") or "")
        elif draft_out is not None:
            dj = getattr(draft_out, "json", None)
            if isinstance(dj, dict):
                winner_id = str(dj.get("selected_step") or "")

        parsed = _parse_draft(draft)
        pre_polish_draft = (
            str(parsed.get("draft") or "") if parsed is not None else ""
        )
        if parsed is not None and str(channel or "") == "email":
            intake_block = (
                assemble_json.get("intake") if isinstance(assemble_json, dict) else None
            )
            intake_dict = intake_block if isinstance(intake_block, dict) else {}
            recipient = str(
                intake_dict.get("recipient")
                or (assemble_json or {}).get("recipient")
                or ""
            )
            signer = str(intake_dict.get("signer") or "")
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
                ledger, parsed, judge_dict, winner_id=winner_id or None
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
            if max_chars is not None or channel is not None:
                violations.extend(
                    check_fact_ceiling(
                        ledger,
                        parsed["draft"],
                        max_chars=max_chars,
                        channel=channel,
                    )
                )
        if prior is not None and parsed is not None and not judge_dict.get(
            "judge_unavailable"
        ):
            v2_clauses = list(judge_dict.get("clauses") or [])
            prior_for_delta: Any = prior
            if str(channel or "") == "email":
                intake_block = (
                    assemble_json.get("intake")
                    if isinstance(assemble_json, dict)
                    else None
                )
                intake_dict = intake_block if isinstance(intake_block, dict) else {}
                recipient = str(
                    intake_dict.get("recipient")
                    or (assemble_json or {}).get("recipient")
                    or ""
                )
                signer = str(intake_dict.get("signer") or "")
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
                    v2_clauses=v2_clauses,
                    pin_ledger=ledger,
                    channel=channel,
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
        added_facts = added_facts_from_judge_clauses(
            list(judge_dict.get("clauses") or [])
        )
        effective_channel = str(channel or "email")
        purpose_points: list[str] = []
        purpose_text = ""
        intake_answers: list[str] = []
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
        flagged = drift.get("flagged_sentences") or []
        purpose_drops = [
            {"sentence": row["sentence"], "reason": "not_purpose_relevant"}
            for row in flagged
            if isinstance(row, dict) and row.get("reason") == "not_purpose_relevant"
        ]
        if added_facts:
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
