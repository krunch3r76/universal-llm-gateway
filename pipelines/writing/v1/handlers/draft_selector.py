"""Deterministic multi-weight draft selector for writer-specialist-v1.

Assemble and the six drafter steps are the callers. Ranking is requested
signer band, then pin coverage minus email penalty,
then earlier drafter order. Drafts that still contain an unresolved
``[NEED:]`` token are ineligible. The handler does not call a model or write
a file.
"""

from __future__ import annotations

import json
import re
from typing import Any

from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput
from writing_intake.source_premise import unsupported_source_claims

from ._obligation_coverage import (
    actionable_obligations,
    coverage_map_for_draft,
    obligation_coverage_score,
)
from .draft_selector_signer import (
    requested_signer as _requested_signer,
)
from .draft_selector_signer import (
    signer_band as _signer_band,
)
from .draft_selector_signer import (
    signer_verbatim_required as _signer_verbatim_required,
)
from .email_envelope import email_draft_penalty, email_restate_pin_satisfied
from .finalize_support import _omit_with_reason_ids
from .independence import load_writing_models, model_family
from .seat_wait import sanitize_plain_text_draft

_DRAFTER_STEPS: tuple[tuple[str, str], ...] = (
    ("drafter_openai", "drafter_openai"),
    ("drafter_google", "drafter_google"),
    ("drafter_xai", "drafter_xai"),
    ("drafter_local_hermes", "drafter_local_hermes"),
    ("drafter_local_ernie", "drafter_local_ernie"),
    ("drafter_anthropic", "drafter_anthropic"),
)

_TOKEN = re.compile(r"[a-z0-9]+")
_NEED_TOKEN = re.compile(r"\[NEED:[^\]]*\]")
def _step(payload: dict[str, Any], *, model_id: str | None = None) -> StepOutput:
    return StepOutput(raw=json.dumps(payload), json=payload, model_id=model_id)


def _normalize_tokens(text: str) -> set[str]:
    return set(_TOKEN.findall(text.lower()))


def _judge_pin_score(
    candidate_id: str,
    attested_pins: list[dict[str, Any]],
    judge_pins: list[dict[str, Any]],
    *,
    channel: str,
    draft_text: str,
) -> tuple[int, int]:
    """Return (pin_score, pin_penalty) from semantic_judge rows for one candidate."""
    rows = [
        row
        for row in judge_pins
        if isinstance(row, dict) and str(row.get("candidate_id") or "") == candidate_id
    ]
    score = 0
    penalty = 0
    for pin in attested_pins:
        pin_id = str(pin.get("pin_id") or "")
        if not pin_id:
            continue
        if pin.get("verbatim_required") is True and channel == "email":
            excerpt = str(pin.get("excerpt") or "")
            if excerpt and email_restate_pin_satisfied(draft_text, excerpt):
                score += 1
            continue
        match = next((row for row in rows if str(row.get("pin_id")) == pin_id), None)
        if match is None:
            penalty += 1
            continue
        if match.get("satisfied") is True and match.get("polarity_match") is not False:
            score += 1
        else:
            penalty += 1
    return score, penalty


def _quality_penalty(candidate_id: str, findings: list[dict[str, Any]]) -> int:
    total = 0
    for row in findings:
        if not isinstance(row, dict):
            continue
        if str(row.get("candidate_id") or "") != candidate_id:
            continue
        total += int(row.get("penalty") or 0)
    return total


def _rank_key(pair: tuple[int, dict[str, Any]]) -> tuple[int, int, int, int, int]:
    """Sort eligible drafts: obligation coverage, signer, pins, order."""
    index, row = pair
    pin_tier = int(row["score"]) - int(row["email_penalty"]) - int(row["pin_penalty"])
    return (
        -int(row.get("obligation_coverage_score") or 0),
        -int(row["signer_band"]),
        -pin_tier,
        int(row["pin_penalty"]),
        index,
    )


def _raw_text(step: Any) -> str:
    if step is None:
        return ""
    raw = step.get("raw") if isinstance(step, dict) else getattr(step, "raw", None)
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    if not isinstance(raw, str):
        return ""
    text = raw.strip()
    if text.startswith("{") and '"draft"' in text:
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict) and isinstance(parsed.get("draft"), str):
                return parsed["draft"]
        except json.JSONDecodeError:
            pass
    return text


class WritingDraftSelectorHandler(BaseHandler):
    """Choose one drafter candidate for the downstream fact ceiling.

    Candidates come from context outputs in fixed drafter order. An
    unresolved ``[NEED:]`` token removes a candidate. The winner is the
    highest signer band, then first-person voice, then pin coverage minus
    email penalty, then the earlier drafter. If every candidate is ineligible
    the step refuses
    and does not emit a bus event.
    """

    step_type = "writing_draft_selector_v1"

    async def execute(self, step: Any, context: Any) -> StepOutput:
        """Select a draft from the drafter outputs on ``context``.

        ``step`` is unused. Assemble supplies the pin ledger and the
        requested signer. Returns a StepOutput whose JSON is the winner,
        or a refusal when no eligible draft remains. Does not call a model.
        """
        del step
        outputs = getattr(context, "outputs", {}) or {}
        assemble = outputs.get("assemble")
        assemble_json = (
            assemble.get("json")
            if isinstance(assemble, dict)
            else getattr(assemble, "json", None)
        )
        if not isinstance(assemble_json, dict):
            assemble_json = {}
        ledger = list(assemble_json.get("pin_ledger") or [])
        attested = [pin for pin in ledger if pin.get("attested") is True]
        signer = _requested_signer(assemble_json)
        intake = assemble_json.get("intake")
        channel = str(
            assemble_json.get("channel")
            or (intake.get("channel") if isinstance(intake, dict) else "")
            or ""
        )
        source_message = ""
        if isinstance(intake, dict):
            source_message = str(intake.get("source_message") or "")
        signer_verbatim_required = _signer_verbatim_required(attested, signer=signer)
        aliases = load_writing_models()
        src = outputs.get("source_obligations")
        src_json = (
            src.get("json")
            if isinstance(src, dict)
            else getattr(src, "json", None)
        )
        obligations = list((src_json or {}).get("obligations") or [])
        arch = outputs.get("prompt_architect")
        arch_json = (
            arch.get("json")
            if isinstance(arch, dict)
            else getattr(arch, "json", None)
        )
        architect_brief = str((arch_json or {}).get("architect_brief") or "")
        judge_out = outputs.get("semantic_judge_candidates")
        judge_json = (
            judge_out.get("json")
            if isinstance(judge_out, dict)
            else getattr(judge_out, "json", None)
        )
        judge_json = judge_json if isinstance(judge_json, dict) else {}
        judge_pins = list(judge_json.get("pins") or [])
        judge_clauses = list(judge_json.get("clauses") or [])
        judge_quality = list(judge_json.get("quality_findings") or [])
        seen_candidate_ids = {
            str(row.get("candidate_id") or "")
            for row in judge_pins
            if isinstance(row, dict) and row.get("candidate_id")
        } | {
            str(row.get("candidate_id") or "")
            for row in judge_clauses
            if isinstance(row, dict) and row.get("candidate_id")
        } | {
            str(row.get("candidate_id") or "")
            for row in judge_quality
            if isinstance(row, dict) and row.get("candidate_id")
        }
        seen_candidate_ids.discard("")
        omit_ids = _omit_with_reason_ids(architect_brief)
        actionable = [
            row
            for row in actionable_obligations(obligations)
            if str(row.get("id") or "").upper() not in omit_ids
        ]
        candidates: list[dict[str, Any]] = []
        for step_name, role in _DRAFTER_STEPS:
            out = outputs.get(step_name)
            err = (
                out.get("error")
                if isinstance(out, dict)
                else getattr(out, "error", None)
            )
            if err:
                continue
            if seen_candidate_ids and step_name not in seen_candidate_ids:
                continue
            text = sanitize_plain_text_draft(_raw_text(out))
            if not text.strip():
                continue
            model_id = (
                (
                    out.get("model_id")
                    if isinstance(out, dict)
                    else getattr(out, "model_id", None)
                )
                or aliases.get(role)
                or ""
            )
            unresolved_need = _NEED_TOKEN.search(text) is not None
            premise_violations = unsupported_source_claims(
                source_message=source_message,
                draft_text=text,
            )
            rejected: str | None = None
            if unresolved_need:
                rejected = "unresolved_need"
            elif premise_violations:
                rejected = premise_violations[0]
            obl_score = (
                obligation_coverage_score(text, actionable, pin_ledger=ledger)
                if actionable
                else 0
            )
            cov_map = (
                coverage_map_for_draft(text, actionable, pin_ledger=ledger)
                if actionable
                else []
            )
            pin_score, pin_penalty = _judge_pin_score(
                step_name,
                attested,
                judge_pins,
                channel=channel,
                draft_text=text,
            )
            quality_penalty = _quality_penalty(step_name, judge_quality)
            candidates.append(
                {
                    "role": role,
                    "step": step_name,
                    "draft": text,
                    "model_id": model_id,
                    "family": model_family(str(model_id)),
                    "score": pin_score,
                    "pin_penalty": pin_penalty,
                    "obligation_coverage_score": obl_score,
                    "coverage_map": cov_map,
                    "email_penalty": email_draft_penalty(
                        text,
                        channel=channel,
                        signer=signer,
                        recipient=str(
                            assemble_json.get("recipient")
                            or (
                                intake.get("recipient")
                                if isinstance(intake, dict)
                                else ""
                            )
                            or ""
                        ),
                    )
                    + quality_penalty,
                    "signer_band": _signer_band(
                        text,
                        signer,
                        channel=channel,
                        signer_verbatim_required_flag=signer_verbatim_required,
                    ),
                    "rejected": rejected,
                }
            )

        if not candidates:
            return _step(
                {
                    "ok": False,
                    "refused": "no_draft_candidates",
                    "error": "all drafters failed or empty",
                }
            )
        eligible = [row for row in candidates if row["rejected"] is None]
        if not eligible:
            refused = "unresolved_need_placeholder"
            error = "every candidate contains an unresolved [NEED:] token"
            if all(
                row.get("rejected")
                and str(row["rejected"]).startswith("draft:link_missing")
                for row in candidates
            ):
                refused = "unsupported_source_claim"
                error = (
                    "every candidate claims a missing link unsupported by source_message"
                )
            return _step(
                {
                    "ok": False,
                    "refused": refused,
                    "error": error,
                    "candidates": candidates,
                }
            )

        winner = sorted(enumerate(eligible), key=_rank_key)[0][1]
        return _step(
            {
                "ok": True,
                "refused": None,
                "draft": winner["draft"],
                "selected_role": winner["role"],
                "selected_step": winner["step"],
                "candidates": candidates,
                "coverage_score": winner["score"],
                "obligation_coverage_score": winner.get("obligation_coverage_score", 0),
                "coverage_map": winner.get("coverage_map") or [],
            },
            model_id=str(winner["model_id"]),
        )
