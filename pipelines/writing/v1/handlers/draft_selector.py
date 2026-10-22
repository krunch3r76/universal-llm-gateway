"""Deterministic multi-weight draft selector for writer-specialist-v1.

Assemble and the six drafter steps are the callers. Pin coverage stays the
fact score. A first-person bonus breaks near ties. A requested signer
outranks both. Drafts that still contain an unresolved ``[NEED:]`` token
are ineligible. The handler does not call a model or write a file.
"""

from __future__ import annotations

import json
import re
from typing import Any

from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

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


def _step(payload: dict[str, Any], *, model_id: str | None = None) -> StepOutput:
    return StepOutput(raw=json.dumps(payload), json=payload, model_id=model_id)


def _normalize_tokens(text: str) -> set[str]:
    return set(_TOKEN.findall(text.lower()))


def _pin_tokens(pin: dict[str, Any]) -> set[str]:
    excerpt = str(pin.get("excerpt") or "")
    return _normalize_tokens(excerpt)


def _score_draft(draft_text: str, attested_pins: list[dict[str, Any]]) -> int:
    """Count attested pins whose anchor tokens are all present in the draft.

    A pin with an empty excerpt does not score. The count is the fact
    weight only: voice and signer are applied later. No I/O.
    """
    draft_tokens = _normalize_tokens(draft_text)
    score = 0
    for pin in attested_pins:
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
    match = re.search(r"(?m)^Signer:\s*(.+?)\s*$", block)
    if match and match.group(1).strip():
        return match.group(1).strip()
    return ""


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


def _rank_key(pair: tuple[int, dict[str, Any]]) -> tuple[int, int, int]:
    """Sort eligible drafts: signer, then pins plus voice, then drafter order."""
    index, row = pair
    return (
        -int(row["signer_band"]),
        -(int(row["score"]) + int(row["voice_bonus"])),
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
    highest signer band, then pin coverage plus a first-person bonus, then
    the earlier drafter. If every candidate is ineligible the step refuses
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

        aliases = load_writing_models()
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
            candidates.append(
                {
                    "role": role,
                    "step": step_name,
                    "draft": text,
                    "model_id": model_id,
                    "family": model_family(str(model_id)),
                    "score": _score_draft(text, attested),
                    "voice_bonus": 1 if _FIRST_PERSON.search(text) else 0,
                    "signer_band": _signer_band(text, signer),
                    "rejected": "unresolved_need" if unresolved_need else None,
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
            return _step(
                {
                    "ok": False,
                    "refused": "unresolved_need_placeholder",
                    "error": "every candidate contains an unresolved [NEED:] token",
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
            },
            model_id=str(winner["model_id"]),
        )
