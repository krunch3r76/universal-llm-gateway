"""Deterministic provenance check for a writer-specialist draft.

``check_provenance`` is a pure function. ``WritingProvenanceCheckHandler``
wraps it for the ``writing_provenance_check_v1`` step, reading the pin ledger
and draft JSON from the step's handler input bindings. No model call.
"""

from __future__ import annotations

import json
import re
from typing import Any

from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

_PERSONA = re.compile(
    r"^(as an? |i am an? (ai|assistant|language model))",
    re.IGNORECASE,
)
_CLOSERS = (
    "in summary",
    "in conclusion",
    "overall",
    "to summarize",
    "in short",
)
_EXPRESS = frozenset({"express", "imply"})
# A request cites only not-attested pins. Citing an attested pin would let a
# stated fact wear the request label and skip unattested_pin. That is refused.
_REQUEST = "request"
_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_NAME = re.compile(r"\b[A-Z][a-z]{2,}\b")
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
    }
)
_CLAUSE_DETAIL_MAX = 160


def _step(payload: dict[str, Any]) -> StepOutput:
    return StepOutput(raw=json.dumps(payload), json=payload)


def _parse_draft(draft_json: Any) -> dict[str, Any] | None:
    if isinstance(draft_json, str):
        try:
            draft_json = json.loads(draft_json)
        except json.JSONDecodeError:
            return None
    if not isinstance(draft_json, dict):
        return None
    if not isinstance(draft_json.get("draft"), str):
        return None
    return draft_json


def _violation(kind: str, claim_id: str | None, detail: Any) -> dict[str, Any]:
    return {"type": kind, "claim_id": claim_id, "detail": detail}


def check_provenance(
    pin_ledger: list[dict[str, Any]] | None,
    draft_json: Any,
) -> list[dict[str, Any]]:
    """Return provenance violations for one draft against ``pin_ledger``.

    Callers are the provenance handler and its unit tests. The check does
    not read the network or mutate the ledger. A missing or unusable draft
    yields a single ``draft_unparseable`` row and no further checks.
    """
    parsed = _parse_draft(draft_json)
    if parsed is None:
        return [_violation("draft_unparseable", None, "malformed")]
    ledger = pin_ledger or []
    by_id = {
        str(pin.get("pin_id")): pin
        for pin in ledger
        if isinstance(pin, dict) and pin.get("pin_id")
    }
    violations: list[dict[str, Any]] = []
    claims = parsed.get("claims")
    if not isinstance(claims, list):
        claims = []
    for claim in claims:
        if not isinstance(claim, dict):
            continue
        claim_id = claim.get("claim_id")
        claim_id_s = str(claim_id) if claim_id is not None else None
        pin_ids = claim.get("pin_ids")
        if not isinstance(pin_ids, list):
            pin_ids = []
        disposition = str(claim.get("disposition") or "")
        text = str(claim.get("text") or "")
        for pin_id in pin_ids:
            key = str(pin_id)
            pin = by_id.get(key)
            if pin is None:
                violations.append(_violation("unknown_pin", claim_id_s, key))
                continue
            attested = pin.get("attested") is True
            if disposition in _EXPRESS and pin.get("attested") is False:
                violations.append(_violation("unattested_pin", claim_id_s, key))
            if disposition == _REQUEST and attested:
                violations.append(_violation("request_cites_attested", claim_id_s, key))
        if (
            not pin_ids
            and disposition != "omit_with_reason"
            and "[unverified:" not in text
            and "[NEED:" not in text
        ):
            violations.append(
                _violation("pinless_claim_unmarked", claim_id_s, disposition or "empty")
            )
    draft_text = parsed["draft"]
    em_count = draft_text.count("\u2014")
    if em_count:
        violations.append(_violation("em_dash", None, em_count))
    for line in draft_text.splitlines():
        if _PERSONA.match(line.strip()):
            violations.append(_violation("persona_line", None, line.strip()))
    paragraphs = [
        part.strip() for part in re.split(r"\n\s*\n", draft_text) if part.strip()
    ]
    if paragraphs:
        last = paragraphs[-1]
        lowered = last.lower()
        if any(lowered.startswith(prefix) for prefix in _CLOSERS):
            violations.append(_violation("bolted_closer", None, last.splitlines()[0]))
    return violations


def _sentences(text: str) -> list[str]:
    """Split on sentence-ending punctuation. Clause granularity is one sentence."""
    return [part.strip() for part in _SENTENCE.split(text) if part.strip()]


def _clause_pinned(sentence: str, claims: list[Any]) -> bool:
    for claim in claims:
        if not isinstance(claim, dict):
            continue
        pins = claim.get("pin_ids")
        if not isinstance(pins, list) or not pins:
            continue
        claim_text = str(claim.get("text") or "").strip()
        if not claim_text:
            continue
        if claim_text in sentence or sentence in claim_text:
            return True
    return False


def revision_delta(
    draft_v1: Any,
    draft_v2: Any,
) -> list[dict[str, Any]]:
    """Diff v2 against v1 at sentence granularity.

    A sentence whose stripped text is absent from v1 and that no v2 claim
    with pins overlaps is ``v2_new_unpinned_clause``. A capitalised token
    of three or more letters present in v1 and absent in v2 is
    ``v2_dropped_name``. The name rule is not NER: sentence-initial words
    that are not in the stop list can false-positive, and lowercase or
    all-caps names are missed.
    """
    prior = _parse_draft(draft_v1)
    current = _parse_draft(draft_v2)
    if prior is None or current is None:
        return []
    v1 = prior["draft"]
    v2 = current["draft"]
    old = set(_sentences(v1))
    claims = current.get("claims")
    if not isinstance(claims, list):
        claims = []
    violations: list[dict[str, Any]] = []
    for sentence in _sentences(v2):
        if sentence in old:
            continue
        if _clause_pinned(sentence, claims):
            continue
        detail = sentence
        if len(detail) > _CLAUSE_DETAIL_MAX:
            detail = detail[:_CLAUSE_DETAIL_MAX]
        violations.append(_violation("v2_new_unpinned_clause", None, detail))
    v2_names = set(_NAME.findall(v2))
    for name in _NAME.findall(v1):
        if name in _NAME_STOP or name in v2_names:
            continue
        violations.append(_violation("v2_dropped_name", None, name))
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
    """Run ``check_provenance`` on the ledger and draft bound to this step.

    The same class serves the initial check and the post-revision check.
    Bindings name which prior step supplies the draft. When ``draft_v1`` is
    bound, sentence-level new clauses without pins and dropped capitalised
    names are appended. The handler returns ``pass`` true only when the
    violation list is empty.
    """

    step_type = "writing_provenance_check_v1"

    async def execute(self, step: Any, context: Any) -> StepOutput:
        """Read bound ledger and draft JSON, then return the violation list.

        Missing bindings count as an unparseable draft. The handler does not
        call a model and does not modify prior step outputs.
        """
        ledger = _resolve_binding(step, context, "pin_ledger")
        draft = _resolve_binding(step, context, "draft")
        prior = _resolve_binding(step, context, "draft_v1")
        if not isinstance(ledger, list):
            ledger = []
        violations = check_provenance(ledger, draft)
        if prior is not None:
            violations.extend(revision_delta(prior, draft))
        payload = {"ok": True, "violations": violations, "pass": violations == []}
        return _step(payload)
