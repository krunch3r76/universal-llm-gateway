"""Fact-inventory mode: extract claims from prior_draft and classify load-bearing.

Provenance calls ``run_drift_check``, which delegates content-token drift to
``writing_intake.inventory_drift.fact_token_flags`` when ``load_bearing_claims``
is supplied.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from typing import Any

from .claim_gates import (
    ClaimEvaluation,
    check_profile_for_channel,
    evaluate_candidate_claim,
    is_declaration_register,
    map_claim_to_purpose,
    parse_purpose_points,
)
from .inventory_drift import fact_token_flags

check_class_for_channel = check_profile_for_channel

_ABBREV = re.compile(
    r"\b(?:Mr|Ms|Mrs|Dr|Prof|Sr|Jr|St|No|Nos|Ex|Fig|Para|Paras|Sec|Secs|v|vs|etc|e\.g|i\.e)\.$",
    re.I,
)
_SENT_BOUNDARY = re.compile(r"(?<=[.!?][\"”\x27’)\]])\s+|(?<=[.!?])\s+")
_STANDALONE_EXHIBIT = re.compile(r"^\(Exhibit\s+\d+\.?\)$", re.I)
_HEADER = re.compile(r"^#{1,6}\s+", re.MULTILINE)
_NUMBERED_SECTION = re.compile(r"^\s*\d+\.\s+", re.MULTILINE)
_SIGNATURE_MARKERS = (
    "i declare under penalty",
    "executed on",
    "signature:",
    "/s/",
)
_NON_LOAD_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b\d+\s+days after\b", re.I), "day_count_arithmetic"),
    (
        re.compile(
            r"Paragraphs?\s+\d+\s+through\s+\d+\s+above|as stated (?:in paragraph|above)",
            re.I,
        ),
        "self_referential_pointer",
    ),
    (
        re.compile(
            r"this declaration sets out|Paragraphs?\s+\d+\s+through\s+\d+",
            re.I,
        ),
        "self_referential_pointer",
    ),
    (
        re.compile(r"\brev\s+\d|not legal advice|attorney review|\bdraft\b", re.I),
        "process_chat_residue",
    ),
]


def _strip_non_sworn_body(text: str) -> str:
    lines = text.splitlines()
    has_sworn_opener = any(
        re.match(r"^I,\s+.+,\s+state:", ln.strip(), re.I) for ln in lines
    )
    out: list[str] = []
    in_sworn = not has_sworn_opener
    for line in lines:
        stripped = line.strip()
        lower = stripped.lower()
        if _HEADER.match(stripped):
            if re.match(r"^#{1,6}\s*[2-9][\.\s]", stripped):
                in_sworn = False
            continue
        if re.match(r"^I,\s+.+,\s+state:", stripped, re.I):
            in_sworn = True
            continue
        if not in_sworn:
            continue
        if stripped and any(
            marker in lower
            for marker in _SIGNATURE_MARKERS
            if marker != "i declare under penalty"
        ):
            break
        if (
            lower.startswith("i declare under penalty of perjury under the laws")
            or lower.startswith("executed on ")
            or lower.startswith("signature:")
        ):
            break
        if stripped.startswith("---") or stripped.startswith("***"):
            continue
        cleaned = _NUMBERED_SECTION.sub("", stripped)
        out.append(cleaned)
    return "\n".join(out)


def _split_sentences(text: str) -> list[str]:
    """Split text into sentences without breaking on abbreviations or trailing exhibit tags."""
    parts = _SENT_BOUNDARY.split(text.strip())
    merged: list[str] = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if merged and (_ABBREV.search(merged[-1]) or _STANDALONE_EXHIBIT.match(part)):
            merged[-1] = f"{merged[-1]} {part}"
        else:
            merged.append(part)
    return merged


def _measure_sworn_body_words(text: str) -> int:
    """Count words in the sworn body from 'I, ..., state:' to the closing jurat."""
    lines = text.splitlines()
    has_sworn_opener = any(
        re.match(r"^I,\s+.+,\s+state:", ln.strip(), re.I) for ln in lines
    )
    if not has_sworn_opener:
        return len(_strip_non_sworn_body(text).split())
    sworn_lines: list[str] = []
    in_sworn = False
    for line in lines:
        stripped = line.strip()
        lower = stripped.lower()
        if re.match(r"^I,\s+.+,\s+state:", stripped, re.I):
            in_sworn = True
            sworn_lines.append(stripped)
            continue
        if not in_sworn:
            continue
        if (
            lower.startswith("i declare under penalty of perjury under the laws")
            or lower.startswith("executed on ")
            or lower.startswith("signature:")
            or _HEADER.match(stripped)
            or stripped.startswith("---")
            or stripped.startswith("***")
        ):
            break
        if stripped:
            sworn_lines.append(stripped)
    return len(" ".join(sworn_lines).split())


def extract_candidate_claims(prior_draft: str) -> list[str]:
    """Split prior_draft into candidate factual sentences."""
    body = _strip_non_sworn_body(prior_draft)
    chunks: list[str] = []
    for para in re.split(r"\n\s*\n", body):
        para = para.strip()
        if not para or para.startswith("#"):
            continue
        for sent in _split_sentences(para):
            sent = sent.strip()
            if len(sent) < 12:
                continue
            chunks.append(sent)
    return chunks


def classify_claim_load_bearing(
    claim: str,
    *,
    purpose: str,
    audience: str,
    purpose_points: list[str] | None = None,
    intake_answers: list[str] | None = None,
    fact_sources: list[dict[str, Any]] | None = None,
    verbatim_notes: list[str] | None = None,
    attested_sources: list[str] | None = None,
    strict_attestation: bool | None = None,
) -> tuple[bool, str | None]:
    """Return (load_bearing, reason_if_dropped)."""
    del audience
    for pattern, reason in _NON_LOAD_PATTERNS:
        if pattern.search(claim):
            return False, reason
    parsed_points = parse_purpose_points(purpose, purpose_points)
    notes = list(verbatim_notes or [])
    attested = list(attested_sources or [])
    strict = (
        strict_attestation
        if strict_attestation is not None
        else bool(notes or attested)
    )
    evaluation = evaluate_candidate_claim(
        claim,
        purpose_points=parsed_points,
        verbatim_notes=notes,
        attested_sources=attested,
        intake_answers=list(intake_answers or []),
        fact_sources=list(fact_sources or []),
        strict_attestation=strict,
    )
    if evaluation.load_bearing:
        return True, None
    return False, evaluation.reason


def build_fact_inventory(
    prior_draft: str,
    *,
    purpose: str,
    audience: str,
    channel: str = "filing",
    attested_sources: list[str] | None = None,
    verbatim_notes: list[str] | None = None,
    purpose_points: list[str] | None = None,
    intake_answers: list[str] | None = None,
    fact_sources: list[dict[str, Any]] | None = None,
    strict_attestation: bool | None = None,
) -> dict[str, Any]:
    """Classify claims and produce load-bearing inventory + dropped_facts with provenance."""
    del audience
    channel_profile = check_profile_for_channel(channel)
    attested_sources = list(attested_sources or [])
    notes = list(verbatim_notes or []) if verbatim_notes is not None else []
    strict = (
        strict_attestation
        if strict_attestation is not None
        else verbatim_notes is not None or bool(attested_sources)
    )
    parsed_points = parse_purpose_points(purpose, purpose_points)
    checked_body = _strip_non_sworn_body(prior_draft)
    candidates = extract_candidate_claims(prior_draft)
    load_bearing: list[str] = []
    dropped_facts: list[dict[str, Any]] = []
    claim_ledger: list[dict[str, Any]] = []
    context_facts: list[dict[str, Any]] = []
    for claim in candidates:
        clerical_reason: str | None = None
        for pattern, reason in _NON_LOAD_PATTERNS:
            if pattern.search(claim):
                clerical_reason = reason
                break
        if clerical_reason:
            evaluation = ClaimEvaluation(
                False, clerical_reason, "prior_draft_only", None
            )
        else:
            evaluation = evaluate_candidate_claim(
                claim,
                purpose_points=parsed_points,
                verbatim_notes=notes,
                attested_sources=attested_sources,
                intake_answers=list(intake_answers or []),
                fact_sources=list(fact_sources or []),
                strict_attestation=strict,
            )
        ledger_row = {
            "claim": claim,
            "provenance": evaluation.provenance,
            "purpose_point": evaluation.purpose_point,
        }
        if evaluation.load_bearing:
            load_bearing.append(claim)
            claim_ledger.append(ledger_row)
        elif evaluation.provenance == "prior_draft_only":
            claim_ledger.append(ledger_row)
        if not evaluation.load_bearing and evaluation.reason:
            row: dict[str, Any] = {
                "claim": claim,
                "reason": evaluation.reason,
                "provenance": evaluation.provenance,
            }
            dropped_facts.append(row)
            if evaluation.provenance == "intake_answer":
                context_facts.append(row)
    attested = list(dict.fromkeys([*notes, *attested_sources]))
    result: dict[str, Any] = {
        "input_sha256": hashlib.sha256(prior_draft.encode("utf-8")).hexdigest(),
        "checked_sha256": hashlib.sha256(checked_body.encode("utf-8")).hexdigest(),
        "input_word_count": len(prior_draft.split()),
        "checked_word_count": len(checked_body.split()),
        "sentence_count": len(candidates),
        "load_bearing_claims": load_bearing,
        "dropped_facts": dropped_facts,
        "claim_ledger": claim_ledger,
        "context_facts": context_facts,
        "verbatim_notes": notes,
        "attested_sources": attested,
        "check_profile": channel_profile,
    }
    if channel_profile == "sworn":
        result["sworn_body_word_count"] = _measure_sworn_body_words(prior_draft)
    else:
        result["body_word_count"] = len(checked_body.split())
    return result


_EXHIBIT_TAG = re.compile(r"\(Exhibit\s+\d+\.?\)", re.I)
_EXHIBIT_LEDGER = re.compile(r"Exhibit\s+(\d+)", re.I)

_DRIFT_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(
            r"Paragraphs?\s+\d+\s+through\s+\d+\s+above|as stated in paragraph|this declaration sets out",
            re.I,
        ),
        "self_reference",
    ),
    (
        re.compile(r"\brev\s+[56]|not legal advice|attorney review", re.I),
        "process_chat_residue",
    ),
]


_HEADER_LINE = re.compile(
    r"^\s*(?:to|cc|bcc|from|subject|date)\s*:",
    re.I,
)
_SALUTATION = re.compile(
    r"^(?:dear|hello|hi|greetings|to whom it may concern)\b",
    re.I,
)
_VALEDICTION = re.compile(
    r"^(?:thank you|thanks|sincerely|regards|best regards|kind regards|best|"
    r"respectfully|cordially|yours truly|warm regards)[\s,.!]*$",
    re.I,
)
_LIST_MARKER = re.compile(r"^\s*(?:\d+[.)]|[-*•])\s+")
_EXHIBIT_LINE = re.compile(r"^\s*Exhibit\s+\d+\s*:", re.I)
_EXHIBIT_LEADIN = re.compile(
    r"^(?:attached are(?:\s+the\s+following(?:\s+exhibits|\s+documents)?)?|"
    r"the\s+(?:attached\s+)?exhibits\s+are(?:\s+as\s+follows)?|"
    r"enclosed are(?:\s+the\s+following(?:\s+exhibits)?)?)\s*:\s*$",
    re.I,
)
_POSTSCRIPT = re.compile(r"^\s*P\.?\s*S\.?\b", re.I)


def _append_units_from_text(units: list[str], text: str) -> None:
    for sent in _split_sentences(text):
        sent = sent.strip()
        if len(sent) >= 12:
            units.append(sent)


def _correspondence_units(text: str) -> list[str]:
    units: list[str] = []
    paragraph_lines: list[str] = []
    past_valediction = False

    def flush_paragraph() -> None:
        nonlocal paragraph_lines
        if not paragraph_lines:
            return
        list_lines = [ln for ln in paragraph_lines if _LIST_MARKER.match(ln)]
        if len(list_lines) >= 2:
            for line in paragraph_lines:
                if _LIST_MARKER.match(line):
                    stripped = _LIST_MARKER.sub("", line.strip()).strip()
                    if len(stripped) >= 12:
                        units.append(stripped)
        else:
            joined = " ".join(ln.strip() for ln in paragraph_lines if ln.strip())
            _append_units_from_text(units, joined)
        paragraph_lines = []

    for line in text.splitlines():
        stripped = line.strip()
        if _HEADER_LINE.match(line):
            continue
        if stripped and _EXHIBIT_LINE.match(stripped):
            continue
        if stripped and _EXHIBIT_LEADIN.match(stripped):
            continue
        if stripped and len(stripped) <= 120 and _SALUTATION.match(stripped):
            if "," in stripped:
                after_comma = stripped.split(",", 1)[1].strip()
                if len(after_comma.split()) > 4:
                    _append_units_from_text(units, stripped)
            continue
        if past_valediction:
            if stripped and _POSTSCRIPT.match(stripped):
                _append_units_from_text(units, stripped)
            continue
        if stripped and _VALEDICTION.match(stripped):
            past_valediction = True
            flush_paragraph()
            continue
        if not stripped:
            flush_paragraph()
            continue
        paragraph_lines.append(line)
    flush_paragraph()
    return units


def _verbatim_required_exempts_sentence(
    sentence: str,
    verbatim_required_excerpts: Sequence[str],
) -> bool:
    """True when a verbatim-required excerpt legitimately covers this sentence."""
    s_norm = sentence.strip().lower()
    if not s_norm:
        return False
    for excerpt in verbatim_required_excerpts:
        ex_norm = str(excerpt).strip().lower()
        if not ex_norm:
            continue
        s_cmp = s_norm.rstrip(".!?").strip()
        ex_cmp = ex_norm.rstrip(".!?").strip()
        if s_cmp == ex_cmp:
            return True
        if len(ex_cmp) < 10:
            continue
        if s_norm in ex_norm or ex_norm in s_norm:
            return True
    return False


def drift_check_sentence(
    sentence: str,
    *,
    channel: str,
    exhibit_ids: set[str],
    purpose_points: Sequence[str] | None = None,
    parsed_purpose_points: list | None = None,
    intake_answers: Sequence[str] | None = None,
    verbatim_required_excerpts: Sequence[str] = (),
) -> str | None:
    """Return drift reason if sentence should be flagged."""
    points = parsed_purpose_points or (
        parse_purpose_points("", list(purpose_points or [])) if purpose_points else []
    )
    for answer in intake_answers or []:
        if answer.strip() and answer.strip() in sentence:
            return "intake_answer_promoted"
    for pattern, reason in _DRIFT_PATTERNS:
        if pattern.search(sentence):
            return reason
    profile = check_profile_for_channel(channel)
    if profile == "correspondence" and is_declaration_register(sentence):
        exempt = _verbatim_required_exempts_sentence(
            sentence, verbatim_required_excerpts
        )
        if not exempt:
            return "declaration_register_in_correspondence"
    if profile == "sworn" and exhibit_ids:
        if _EXHIBIT_LEDGER.search(sentence) and not _EXHIBIT_TAG.search(sentence):
            return "exhibit_fact_missing_inline_tag"
    if points and map_claim_to_purpose(sentence, points) is None:
        if not _verbatim_required_exempts_sentence(
            sentence, verbatim_required_excerpts
        ):
            return "not_purpose_relevant"
    return None


def run_drift_check(
    draft_text: str,
    *,
    channel: str,
    pin_ledger: list[dict[str, Any]] | None = None,
    strip_non_sworn: bool = True,
    purpose_points: Sequence[str] | None = None,
    purpose: str | None = None,
    intake_answers: Sequence[str] | None = None,
    load_bearing_claims: list[str] | None = None,
) -> dict[str, Any]:
    """Sentence-level drift check; branches on channel profile."""
    check_class = check_profile_for_channel(channel)
    points = [str(p).strip() for p in (purpose_points or []) if str(p).strip()]
    if points:
        parsed_points = parse_purpose_points(purpose or "", points)
    elif purpose and str(purpose).strip():
        parsed_points = parse_purpose_points(str(purpose).strip(), None)
    else:
        parsed_points = []
    purpose_gate = (
        "applied" if parsed_points else "skipped_no_purpose_points"
    )
    verbatim_required_excerpts = [
        str(pin["excerpt"])
        for pin in (pin_ledger or [])
        if isinstance(pin, dict) and pin.get("verbatim_required") is True
    ]
    exhibit_ids: set[str] = set()
    if pin_ledger:
        for pin in pin_ledger:
            excerpt = str(pin.get("excerpt") or "")
            for match in _EXHIBIT_LEDGER.finditer(excerpt):
                exhibit_ids.add(match.group(1))
    if check_class == "correspondence":
        units = _correspondence_units(draft_text)
        checked_body = "\n".join(units)
    else:
        checked_body = (
            _strip_non_sworn_body(draft_text) if strip_non_sworn else draft_text
        )
    flagged: list[dict[str, str]] = []
    seen_propositions: set[str] = set()
    sentence_count = 0
    if check_class == "correspondence":
        iter_units = units
    else:
        iter_units = []
        for para in re.split(r"\n\s*\n", checked_body):
            iter_units.extend(_split_sentences(para.strip()))
    for sent in iter_units:
        sent = sent.strip()
        if not sent:
            continue
        sentence_count += 1
        reason = drift_check_sentence(
            sent,
            channel=channel,
            exhibit_ids=exhibit_ids,
            purpose_points=points or None,
            parsed_purpose_points=parsed_points,
            intake_answers=intake_answers,
            verbatim_required_excerpts=verbatim_required_excerpts,
        )
        if reason:
            flagged.append({"sentence": sent, "reason": reason})
            continue
        normalized = re.sub(r"\s+", " ", sent.lower())
        normalized = re.sub(r"\(exhibit\s+\d+\.?\)", "", normalized, flags=re.I)
        if len(normalized) > 40 and normalized in seen_propositions:
            flagged.append({"sentence": sent, "reason": "re_explanation_same_draft"})
        seen_propositions.add(normalized)
    if load_bearing_claims:
        flagged.extend(
            fact_token_flags(
                checked_body,
                [str(item) for item in load_bearing_claims],
            )
        )
    result: dict[str, Any] = {
        "passed": not flagged,
        "input_sha256": hashlib.sha256(draft_text.encode("utf-8")).hexdigest(),
        "checked_sha256": hashlib.sha256(checked_body.encode("utf-8")).hexdigest(),
        "input_word_count": len(draft_text.split()),
        "checked_word_count": len(checked_body.split()),
        "sentence_count": sentence_count,
        "flagged_sentences": flagged,
        "channel": channel,
        "check_class": check_class,
        "check_profile": check_class,
        "purpose_gate": purpose_gate,
    }
    if check_class == "sworn":
        result["sworn_body_word_count"] = _measure_sworn_body_words(draft_text)
    else:
        result["body_word_count"] = len(checked_body.split())
    return result
