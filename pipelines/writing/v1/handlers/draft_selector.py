"""Deterministic multi-weight draft selector for writer-specialist-v1.

Assemble and the six drafter steps are the callers. Ranking is requested
signer band, then first-person voice, then pin coverage minus email penalty,
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
from .email_envelope import (
    correspondence_register_penalty,
    email_draft_penalty,
    email_restate_pin_satisfied,
)
from .finalize import _omit_with_reason_ids
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
_FIRST_PERSON = re.compile(r"\b(?:i|i'm|i've|i'd|i'll|my|me|mine|myself)\b", re.I)
_DECLARANT = re.compile(r"\bI,\s+([^,\n]{2,80}),\s+state\b")
_DECLINE_PIN_HINT = re.compile(
    r"\b(?:declin(?:e|ing|ed)|not\s+available|unavailab)",
    re.I,
)
_NATURAL_DECLINE = re.compile(
    r"\b(?:i\s+)?(?:can't|cannot|won't|will\s+not)\s+(?:take|cover)\b"
    r"|\b(?:i\s+)?(?:can't|cannot|won't|will\s+not)\s+be\s+able\s+to\s+(?:take|cover)\b"
    r"|\bunable\s+to\s+(?:take|cover)\b",
    re.I,
)


def _step(payload: dict[str, Any], *, model_id: str | None = None) -> StepOutput:
    return StepOutput(raw=json.dumps(payload), json=payload, model_id=model_id)


def _normalize_tokens(text: str) -> set[str]:
    return set(_TOKEN.findall(text.lower()))


def _pin_tokens(pin: dict[str, Any]) -> set[str]:
    excerpt = str(pin.get("excerpt") or "")
    return _normalize_tokens(excerpt)


def _draft_satisfies_decline_pin(draft_text: str, excerpt: str) -> bool:
    """Match decline/unavailability pins via natural phrasing or literal anchors."""
    if _NATURAL_DECLINE.search(draft_text):
        return True
    anchors = _pin_tokens({"excerpt": excerpt})
    return bool(anchors and anchors <= _normalize_tokens(draft_text))


def _score_draft(
    draft_text: str,
    attested_pins: list[dict[str, Any]],
    *,
    channel: str = "",
) -> int:
    """Count attested pins whose anchor tokens are all present in the draft.

    A pin with an empty excerpt does not score. The count is the fact
    weight only: voice and signer are applied later. No I/O.
    """
    draft_tokens = _normalize_tokens(draft_text)
    score = 0
    for pin in attested_pins:
        excerpt = str(pin.get("excerpt") or "")
        if channel == "email" and pin.get("restate") is True and excerpt:
            if email_restate_pin_satisfied(draft_text, excerpt):
                score += 1
            continue
        if _DECLINE_PIN_HINT.search(excerpt):
            if _draft_satisfies_decline_pin(draft_text, excerpt):
                score += 1
            continue
        anchors = _pin_tokens(pin)
        if anchors and anchors <= draft_tokens:
            score += 1
    return score


def _requested_signer(assemble_json: dict[str, Any]) -> str:
    """Return the signer the caller asked for, or an empty string.

    Looks at ``signer``, then ``intake.signer``, then ``brief.signer``,
    then a ``Signer:`` line in ``brief_block``. Empty means no signer
    preference. Does not read the network.
    """
    intake = assemble_json.get("intake")
    brief = assemble_json.get("brief")
    sources = [
        assemble_json.get("signer"),
        intake.get("signer") if isinstance(intake, dict) else None,
        brief.get("signer") if isinstance(brief, dict) else None,
    ]
    for source in sources:
        if isinstance(source, str) and source.strip():
            return source.strip()
    block = str(assemble_json.get("brief_block") or "")
    match = re.search(r"(?m)^Signer:[ \t]*([^\n]*?)[ \t]*$", block)
    if not match:
        return ""
    token = match.group(1).strip()
    if not token or token.casefold() == "none":
        return ""
    return token


def _signer_band(draft_text: str, signer: str) -> int:
    """Score whether the draft speaks as the requested signer.

    ``1`` when the signer token appears. ``-1`` when an ``I, Name, state``
    line names someone else. ``0`` when no signer was requested, or the
    draft neither names the signer nor a conflicting declarant. No I/O.
    """
    if not signer:
        return 0
    if re.search(rf"\b{re.escape(signer)}\b", draft_text, re.I):
        return 1
    declarant = _DECLARANT.search(draft_text)
    if declarant and signer.casefold() not in declarant.group(1).casefold():
        return -1
    return 0


def _rank_key(pair: tuple[int, dict[str, Any]]) -> tuple[int, int, int, int, int]:
    """Sort eligible drafts: obligation coverage, signer, voice, pins, order."""
    index, row = pair
    pin_tier = int(row["score"]) - int(row["email_penalty"])
    return (
        -int(row.get("obligation_coverage_score") or 0),
        -int(row["signer_band"]),
        -int(row["voice_bonus"]),
        -pin_tier,
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
        standing_limits = ""
        source_message = ""
        verbatim_notes_blob = ""
        objective = str(
            assemble_json.get("objective")
            or (intake.get("objective") if isinstance(intake, dict) else "")
            or ""
        )
        if isinstance(intake, dict):
            standing_limits = str(intake.get("standing_limits") or "")
            source_message = str(intake.get("source_message") or "")
            notes = intake.get("verbatim_notes") or []
            if isinstance(notes, list):
                verbatim_notes_blob = "\n".join(str(n) for n in notes)
            else:
                verbatim_notes_blob = str(notes or "")
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
            candidates.append(
                {
                    "role": role,
                    "step": step_name,
                    "draft": text,
                    "model_id": model_id,
                    "family": model_family(str(model_id)),
                    "score": _score_draft(text, attested, channel=channel),
                    "obligation_coverage_score": obl_score,
                    "coverage_map": cov_map,
                    "voice_bonus": 1 if _FIRST_PERSON.search(text) else 0,
                    "email_penalty": email_draft_penalty(
                        text,
                        channel=channel,
                        signer=signer,
                        standing_limits=standing_limits,
                        recipient=str(
                            assemble_json.get("recipient")
                            or (
                                intake.get("recipient")
                                if isinstance(intake, dict)
                                else ""
                            )
                            or ""
                        ),
                        objective=objective,
                        verbatim_notes=verbatim_notes_blob,
                    )
                    + (
                        correspondence_register_penalty(
                            text,
                            channel=channel,
                            objective=objective,
                            standing_limits=standing_limits,
                            verbatim_notes=verbatim_notes_blob,
                        )
                        if channel != "email"
                        else 0
                    ),
                    "signer_band": _signer_band(text, signer),
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
