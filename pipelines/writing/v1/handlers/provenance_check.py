"""Deterministic provenance and fact-ceiling checks for writer-specialist drafts."""

from __future__ import annotations

import json
import re
from typing import Any, Literal

from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput
from writing_intake.fact_inventory import run_drift_check

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
_NARRATIVE_CLOSE = (
    "looking forward",
    "thank you for your time",
    "hope this finds you well",
)
_EXPRESS = frozenset({"express", "imply"})
_REQUEST = "request"
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
    }
)
_FUNCTION_WORDS = frozenset(
    {
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
    }
)
_CLAUSE_DETAIL_MAX = 160
_HEDGE = re.compile(
    r"\b(might|perhaps|arguably|seems to|appears to|we believe)\b",
    re.IGNORECASE,
)
_CAUSAL_CONNECTIVES_RE = re.compile(
    r"\b(?:because|therefore|consequently|as a result|since|so that)\b"
    r"|(?:^|(?<=[.!?])\s+|(?<=,)\s*)\bso\b(?!\s+that\b)",
    re.IGNORECASE | re.MULTILINE,
)
_TOKEN = re.compile(r"[a-z0-9]+")
_REQUEST_VERBS = re.compile(
    r"\b(could you|can you|please confirm|please send|would you)\b",
    re.IGNORECASE,
)


def _step(payload: dict[str, Any]) -> StepOutput:
    return StepOutput(raw=json.dumps(payload), json=payload)


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
    if "draft" not in draft_json:
        return None
    if isinstance(draft_json.get("draft"), str):
        out = dict(draft_json)
        out.setdefault("claims", [])
        return out
    return None


def _violation(kind: str, claim_id: str | None, detail: Any) -> dict[str, Any]:
    return {"type": kind, "claim_id": claim_id, "detail": detail}


def classify_sentence_modality(
    sentence: str,
    not_attested_pins: list[dict[str, Any]],
) -> Literal["request", "need_marker", "declarative"]:
    stripped = sentence.strip()
    if "[NEED:" in stripped:
        return "need_marker"
    lowered = stripped.lower()
    if "?" in stripped or _REQUEST_VERBS.search(stripped):
        pin_tokens: set[str] = set()
        for pin in not_attested_pins:
            pin_tokens |= set(_TOKEN.findall(str(pin.get("excerpt") or "").lower()))
        sent_tokens = set(_TOKEN.findall(lowered))
        if pin_tokens & sent_tokens:
            return "request"
    return "declarative"


def check_provenance(
    pin_ledger: list[dict[str, Any]] | None,
    draft_json: Any,
    verifier_clauses: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    parsed = _parse_draft(draft_json)
    if parsed is None:
        return [_violation("draft_unparseable", None, "malformed")]
    ledger = pin_ledger or []
    by_id = {
        str(pin.get("pin_id")): pin
        for pin in ledger
        if isinstance(pin, dict) and pin.get("pin_id")
    }
    not_attested = [pin for pin in ledger if pin.get("attested") is False]
    violations: list[dict[str, Any]] = []
    claims = parsed.get("claims")
    if not isinstance(claims, list):
        claims = []
    draft_text = parsed["draft"]
    if not claims and draft_text.strip():
        for sentence in _sentences(draft_text):
            modality = classify_sentence_modality(sentence, not_attested)
            if modality == "request":
                continue
            if modality == "need_marker":
                continue
            violations.extend(
                _surface_violations_for_sentence(sentence, by_id, not_attested, None)
            )
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
        if not disposition and text:
            disposition = classify_sentence_modality(text, not_attested)
            if disposition == "request":
                disposition = _REQUEST
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
            and disposition != _REQUEST
            and "[unverified:" not in text
            and "[NEED:" not in text
        ):
            violations.append(
                _violation("pinless_claim_unmarked", claim_id_s, disposition or "empty")
            )
    violations.extend(
        check_fact_ceiling(
            ledger,
            draft_text,
            verifier_clauses,
            max_chars=None,
            channel=None,
            include_claim_loop=False,
        )
    )
    return violations


def _surface_violations_for_sentence(
    sentence: str,
    by_id: dict[str, Any],
    not_attested: list[dict[str, Any]],
    claim_id: str | None,
) -> list[dict[str, Any]]:
    violations: list[dict[str, Any]] = []
    modality = classify_sentence_modality(sentence, not_attested)
    if modality == "request":
        return violations
    return violations


def check_fact_ceiling(
    pin_ledger: list[dict[str, Any]] | None,
    draft_text: str,
    verifier_clauses: list[dict[str, Any]] | None = None,
    *,
    max_chars: int | None = None,
    channel: str | None = None,
    include_claim_loop: bool = True,
) -> list[dict[str, Any]]:
    del include_claim_loop
    violations: list[dict[str, Any]] = []
    if max_chars is not None and len(draft_text) > max_chars:
        violations.append(_violation("max_chars_exceeded", None, len(draft_text)))
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
        if channel in {"email", "sms", "portal", "filing", "letter"}:
            if any(phrase in lowered for phrase in _NARRATIVE_CLOSE):
                violations.append(_violation("narrative_close", None, last[:120]))
    clauses = verifier_clauses or []
    violations.extend(_causal_connective_violations(clauses, pin_ledger))
    return violations


def _causal_connective_violations(
    clauses: list[dict[str, Any]],
    pin_ledger: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    violations: list[dict[str, Any]] = []
    for clause in clauses:
        if not isinstance(clause, dict):
            continue
        text = str(clause.get("text") or "")
        pin_ids = clause.get("pin_ids") if isinstance(clause.get("pin_ids"), list) else []
        disposition = str(clause.get("disposition") or "express")
        for pin_id in pin_ids:
            pin = next(
                (row for row in (pin_ledger or []) if str(row.get("pin_id")) == str(pin_id)),
                None,
            )
            if pin is None:
                violations.append(_violation("unknown_pin", None, pin_id))
            elif disposition in _EXPRESS and pin.get("attested") is False:
                violations.append(_violation("unattested_pin", None, pin_id))
        for match in _CAUSAL_CONNECTIVES_RE.finditer(text):
            connective = match.group(0).strip().lower()
            backed = False
            for pin_id in pin_ids:
                pin = next(
                    (
                        row
                        for row in (pin_ledger or [])
                        if str(row.get("pin_id")) == str(pin_id)
                    ),
                    None,
                )
                if pin and connective in str(pin.get("excerpt") or "").lower():
                    backed = True
                    break
            if not backed:
                violations.append(
                    _violation("unbacked_causal_claim", None, match.group(0).strip())
                )
    return violations


def _sentences(text: str) -> list[str]:
    return [part.strip() for part in _SENTENCE.split(text) if part.strip()]


def _tokens(text: str) -> set[str]:
    return set(_TOKEN.findall(text.lower()))


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


def _pin_order(clauses: list[dict[str, Any]]) -> list[str]:
    order: list[str] = []
    for clause in clauses:
        for pin_id in clause.get("pin_ids") or []:
            key = str(pin_id)
            if key not in order:
                order.append(key)
    return order


def _content_anchor_tokens(text: str) -> set[str]:
    return {
        token
        for token in _tokens(text)
        if token not in _FUNCTION_WORDS and len(token) >= 2
    }


def _pin_excerpt_anchor_tokens(
    pin_id: str,
    pin_ledger: list[dict[str, Any]] | None,
) -> set[str]:
    ledger = pin_ledger or []
    pin = next((row for row in ledger if str(row.get("pin_id")) == pin_id), None)
    if not isinstance(pin, dict):
        return set()
    excerpt = str(pin.get("excerpt") or "")
    if not excerpt.strip():
        return set()
    return _content_anchor_tokens(excerpt)


def _pin_clause_anchor_tokens(
    pin_id: str,
    v1_clauses: list[dict[str, Any]],
) -> set[str]:
    for clause in v1_clauses:
        pin_ids = clause.get("pin_ids") if isinstance(clause.get("pin_ids"), list) else []
        if pin_id in {str(item) for item in pin_ids}:
            return _content_anchor_tokens(str(clause.get("text") or ""))
    return set()


def _pin_anchor_tokens(
    pin_id: str,
    pin_ledger: list[dict[str, Any]] | None,
    v1_clauses: list[dict[str, Any]],
) -> set[str]:
    excerpt_anchors = _pin_excerpt_anchor_tokens(pin_id, pin_ledger)
    if excerpt_anchors:
        return excerpt_anchors
    return _pin_clause_anchor_tokens(pin_id, v1_clauses)


def _pin_matches_text(
    text: str,
    pin_id: str,
    pin_ledger: list[dict[str, Any]] | None,
    v1_clauses: list[dict[str, Any]],
) -> bool:
    sent_tokens = _tokens(text)
    excerpt_anchors = _pin_excerpt_anchor_tokens(pin_id, pin_ledger)
    if excerpt_anchors and excerpt_anchors <= sent_tokens:
        return True
    clause_anchors = _pin_clause_anchor_tokens(pin_id, v1_clauses)
    if clause_anchors and clause_anchors <= sent_tokens:
        return True
    return False


def _token_start_offsets(text: str) -> dict[str, int]:
    lowered = text.lower()
    offsets: dict[str, int] = {}
    for match in _TOKEN.finditer(lowered):
        token = match.group(0)
        if token not in offsets:
            offsets[token] = match.start()
    return offsets


def _pin_ranking_anchors(
    pin_id: str,
    matched_pin_ids: list[str],
    pin_ledger: list[dict[str, Any]] | None,
    v1_clauses: list[dict[str, Any]],
) -> set[str]:
    anchors = _pin_anchor_tokens(pin_id, pin_ledger, v1_clauses)
    if len(matched_pin_ids) <= 1:
        return anchors
    shared: set[str] = set()
    for other_id in matched_pin_ids:
        if other_id == pin_id:
            continue
        shared |= _pin_anchor_tokens(other_id, pin_ledger, v1_clauses)
    unique = anchors - shared
    return unique if unique else anchors


def _first_anchor_offset(
    text: str,
    pin_id: str,
    matched_pin_ids: list[str],
    pin_ledger: list[dict[str, Any]] | None,
    v1_clauses: list[dict[str, Any]],
) -> int:
    token_offsets = _token_start_offsets(text)
    candidates = _pin_ranking_anchors(pin_id, matched_pin_ids, pin_ledger, v1_clauses)
    numeric = {token for token in candidates if token.isdigit()}
    if numeric:
        candidates = numeric
    best = len(text) + 1
    for token in candidates:
        idx = token_offsets.get(token)
        if idx is not None and idx < best:
            best = idx
    return best


def _pins_for_text(
    text: str,
    ordered_pin_ids: list[str],
    pin_ledger: list[dict[str, Any]] | None,
    v1_clauses: list[dict[str, Any]],
) -> list[str]:
    matched: list[str] = []
    for pin_id in ordered_pin_ids:
        if _pin_matches_text(text, pin_id, pin_ledger, v1_clauses):
            matched.append(pin_id)
    matched_for_rank = list(matched)
    matched.sort(
        key=lambda pin_id: (
            _first_anchor_offset(text, pin_id, matched_for_rank, pin_ledger, v1_clauses),
            ordered_pin_ids.index(pin_id),
        )
    )
    return matched


def derive_v2_clauses_from_audit(
    draft_text: str,
    v1_clauses: list[dict[str, Any]],
    pin_ledger: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Map v2 prose sentences to pin ids using the audit clause pin order."""
    ordered_pins = _pin_order(v1_clauses)
    sentences = _sentences(draft_text)
    if not sentences:
        return []

    if len(sentences) == 1 and len(v1_clauses) > 1:
        pin_ids = _pins_for_text(draft_text, ordered_pins, pin_ledger, v1_clauses)
        if not pin_ids:
            pin_ids = []
        disposition = str(v1_clauses[0].get("disposition") or "express")
        return [
            {
                "claim_id": "s1",
                "text": draft_text,
                "pin_ids": pin_ids,
                "disposition": disposition,
            }
        ]

    rows: list[dict[str, Any]] = []
    for index, sentence in enumerate(sentences, start=1):
        pin_ids = _pins_for_text(sentence, ordered_pins, pin_ledger, v1_clauses)
        disposition = "express"
        pin_id_set = {str(item) for item in pin_ids}
        for clause in v1_clauses:
            clause_pins = clause.get("pin_ids") if isinstance(clause.get("pin_ids"), list) else []
            if pin_id_set & {str(item) for item in clause_pins}:
                disposition = str(clause.get("disposition") or disposition)
                break
        rows.append(
            {
                "claim_id": f"s{index}",
                "text": sentence,
                "pin_ids": pin_ids,
                "disposition": disposition,
            }
        )
    return rows


def _extract_clauses(draft: dict[str, Any]) -> list[dict[str, Any]]:
    claims = draft.get("claims")
    if isinstance(claims, list) and claims:
        return [row for row in claims if isinstance(row, dict)]
    text = str(draft.get("draft") or "")
    clauses: list[dict[str, Any]] = []
    for index, sentence in enumerate(_sentences(text), start=1):
        clauses.append(
            {
                "claim_id": f"s{index}",
                "text": sentence,
                "pin_ids": [],
                "disposition": "express",
            }
        )
    return clauses


def revision_delta(
    draft_v1: Any,
    draft_v2: Any,
    *,
    v1_clauses: list[dict[str, Any]] | None = None,
    v2_clauses: list[dict[str, Any]] | None = None,
    pin_ledger: list[dict[str, Any]] | None = None,
    v2_clauses_from_derive: bool = False,
) -> list[dict[str, Any]]:
    prior = _parse_draft(draft_v1)
    current = _parse_draft(draft_v2)
    if prior is None or current is None:
        return []
    v1_text = prior["draft"]
    v2_text = current["draft"]
    v1_clause_rows = v1_clauses if v1_clauses is not None else _extract_clauses(prior)
    v2_clause_rows = v2_clauses if v2_clauses is not None else _extract_clauses(current)

    v1_tokens = _tokens(v1_text)
    v2_tokens = _tokens(v2_text)
    violations: list[dict[str, Any]] = []

    v1_order = _pin_order(v1_clause_rows)
    v2_order = _pin_order(v2_clause_rows)
    join_words = set(_FUNCTION_WORDS)
    v1_sentence_set = set(_sentences(v1_text))
    derived_unpinned_new = False
    if v2_clauses_from_derive:
        for row in v2_clause_rows:
            if not isinstance(row, dict):
                continue
            sentence = str(row.get("text") or "").strip()
            if not sentence or sentence in v1_sentence_set:
                continue
            pin_ids = row.get("pin_ids") if isinstance(row.get("pin_ids"), list) else []
            if not pin_ids:
                derived_unpinned_new = True
                break

    if (
        not derived_unpinned_new
        and v1_order
        and v2_order
        and v1_order == v2_order
        and v2_tokens <= (v1_tokens | join_words)
        and len(_sentences(v2_text)) <= len(_sentences(v1_text))
        and all(number in v2_text for number in _NUMBER.findall(v1_text))
        and all(
            name.lower() in v2_text.lower()
            for name in _NAME.findall(v1_text)
            if name not in _NAME_STOP
        )
    ):
        return []

    if v1_order and v2_order and v1_order != v2_order:
        violations.append(_violation("fact_reordered", None, {"v1": v1_order, "v2": v2_order}))

    hedge = _HEDGE.search(v2_text)
    if hedge and not _HEDGE.search(v1_text):
        violations.append(_violation("fact_hedged", None, hedge.group(0)))

    for name in _NAME.findall(v1_text):
        if name in _NAME_STOP:
            continue
        if name.lower() not in v2_text.lower():
            violations.append(_violation("v2_dropped_name", None, name))
            violations.append(_violation("fact_dropped", None, name))

    for number in _NUMBER.findall(v1_text):
        if number not in v2_text:
            violations.append(_violation("v2_dropped_number", None, number))
            violations.append(_violation("fact_dropped", None, number))

    if v2_clauses_from_derive:
        for row in v2_clause_rows:
            if not isinstance(row, dict):
                continue
            sentence = str(row.get("text") or "").strip()
            if not sentence or sentence in v1_sentence_set:
                continue
            pin_ids = row.get("pin_ids") if isinstance(row.get("pin_ids"), list) else []
            if pin_ids:
                continue
            detail = sentence
            if len(detail) > _CLAUSE_DETAIL_MAX:
                detail = detail[:_CLAUSE_DETAIL_MAX]
            violations.append(_violation("v2_new_unpinned_clause", None, detail))
            violations.append(_violation("fact_added", None, detail))

    new_tokens = v2_tokens - v1_tokens - join_words
    if new_tokens:
        for sentence in _sentences(v2_text):
            if sentence in v1_sentence_set:
                continue
            if not v2_clauses_from_derive and _clause_pinned(sentence, v2_clause_rows):
                continue
            if v2_clauses_from_derive:
                row_pins: list[Any] = []
                for row in v2_clause_rows:
                    if not isinstance(row, dict):
                        continue
                    if str(row.get("text") or "").strip() == sentence.strip():
                        raw_pins = row.get("pin_ids")
                        row_pins = raw_pins if isinstance(raw_pins, list) else []
                        break
                if not row_pins:
                    continue
            sent_new = _tokens(sentence) - v1_tokens - join_words
            if not sent_new:
                continue
            detail = sentence
            if len(detail) > _CLAUSE_DETAIL_MAX:
                detail = detail[:_CLAUSE_DETAIL_MAX]
            violations.append(_violation("v2_new_unpinned_clause", None, detail))
            violations.append(_violation("fact_added", None, detail))
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
    step_type = "writing_provenance_check_v1"

    async def execute(self, step: Any, context: Any) -> StepOutput:
        ledger = _resolve_binding(step, context, "pin_ledger")
        draft = _resolve_binding(step, context, "draft")
        prior = _resolve_binding(step, context, "draft_v1")
        if not isinstance(ledger, list):
            ledger = []
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
        verifier_clauses: list[dict[str, Any]] | None = None
        audit_out = outputs.get("fact_ceiling_audit")
        audit_json: Any = None
        if isinstance(audit_out, dict):
            audit_json = audit_out.get("json")
        elif audit_out is not None:
            audit_json = getattr(audit_out, "json", None)
        if isinstance(audit_json, dict):
            raw_clauses = audit_json.get("clauses")
            if isinstance(raw_clauses, list):
                verifier_clauses = raw_clauses

        parsed = _parse_draft(draft)
        violations: list[dict[str, Any]] = []
        if parsed is None:
            violations = [_violation("draft_unparseable", None, "malformed")]
        else:
            violations = check_provenance(ledger, parsed, verifier_clauses)
            if max_chars is not None or channel is not None:
                violations.extend(
                    check_fact_ceiling(
                        ledger,
                        parsed["draft"],
                        verifier_clauses,
                        max_chars=max_chars,
                        channel=channel,
                    )
                )
        if prior is not None:
            v1_clauses = verifier_clauses
            v2_clauses: list[dict[str, Any]] | None = None
            v2_clauses_from_derive = False
            if isinstance(v1_clauses, list) and v1_clauses and parsed is not None:
                v2_clauses = derive_v2_clauses_from_audit(
                    parsed["draft"],
                    v1_clauses,
                    ledger,
                )
                v2_clauses_from_derive = True
                violations.extend(_causal_connective_violations(v2_clauses, ledger))
            violations.extend(
                revision_delta(
                    prior,
                    draft,
                    v1_clauses=v1_clauses,
                    v2_clauses=v2_clauses,
                    pin_ledger=ledger,
                    v2_clauses_from_derive=v2_clauses_from_derive,
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
        added_facts: list[str] = []
        if parsed is not None:
            attested_text = " ".join(
                str(pin.get("excerpt") or "")
                for pin in ledger
                if isinstance(pin, dict) and pin.get("attested")
            ).lower()
            for claim in parsed.get("claims") or []:
                text = str(claim.get("text") or claim) if isinstance(claim, dict) else str(claim)
                token = text.strip().lower()
                if token and token not in attested_text and len(token) > 24:
                    added_facts.append(text.strip())
        drift = {"passed": True, "flagged_sentences": []}
        if parsed is not None:
            drift = run_drift_check(
                str(parsed.get("draft") or ""),
                channel=str(channel or "email"),
                pin_ledger=ledger,
            )
        if added_facts:
            violations.append(
                _violation("added_unattested_fact", None, added_facts[0][:120])
            )
        if not drift.get("passed"):
            violations.append(
                _violation("purpose_drift", None, "drift_check_failed")
            )
        payload = {
            "ok": True,
            "violations": violations,
            "pass": violations == [],
            "dropped_facts": dropped_facts,
            "added_facts": added_facts,
            "drift_check": drift,
        }
        return _step(payload)
