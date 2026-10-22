"""Fact-inventory mode: extract claims from prior_draft and classify load-bearing."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from typing import Any

from .purpose_gate import (
    check_class_for_channel,
    is_declaration_register,
    map_claim_to_purpose,
)

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
    (
        re.compile(
            r"stamp reads|RECEIVED\s+(?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\s+\d",
            re.I,
        ),
        "clerical_intake_stamp",
    ),
    (
        re.compile(r"which the Clerk stamped|Date Filed:\s*\d", re.I),
        "clerical_intake_stamp",
    ),
    (re.compile(r"bill-created date", re.I), "unrequested_bill_created_date"),
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
        re.compile(
            r"side office in the building|orally or in writing|envelope it came in",
            re.I,
        ),
        "redundant_qualifier_recital",
    ),
    (re.compile(r"MANSUBI KAYWAN ETAL|MANSUBI, SHERWIN", re.I), "addressee_block"),
    (
        re.compile(r"At \d{1,2}:\d{2}\s+[AP]M that day, I emailed", re.I),
        "redundant_qualifier_recital",
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


_SUBCLAUSE_TRIMS: list[tuple[re.Pattern[str], str, str]] = [
    (
        re.compile(
            r",\s*addressed to\s+[“\"']?MANSUBI KAYWAN ETAL\s*/\s*MANSUBI,\s*SHERWIN[”\"']?\s+at\s+[^,]+,\s*[^,]+,\s*CA\s+\d{5},",
            re.I,
        ),
        "",
        "addressee_block",
    ),
    (
        re.compile(r",\s*orally or in writing(?=[.,;])", re.I),
        "",
        "redundant_qualifier_recital",
    ),
    (
        re.compile(r"(?<=side office)\s+in the building\b", re.I),
        "",
        "redundant_qualifier_recital",
    ),
    (
        re.compile(
            r",\s*which was stamped\s+['\"“”]?RECEIVED\s+(?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\s+\d+[^'\"“”)]*['\"“”]?",
            re.I,
        ),
        "",
        "clerical_intake_stamp",
    ),
    (
        re.compile(
            r"^(Supplemental tax bills\s+#\d+\s+and\s+#\d+)\s+(each show a bill-created date of\s+[^;]+;\s*)(I do not know the date I received)\s+them\b",
            re.I,
        ),
        r"\3 \1",
        "unrequested_bill_created_date",
    ),
    (
        re.compile(r"\b\d+\s+days after the\s+.+?\s+letter and\s+", re.I),
        "",
        "day_count_arithmetic",
    ),
    (
        re.compile(r",\s*which the Clerk stamped that day\b", re.I),
        "",
        "clerical_intake_stamp",
    ),
]


def _trim_non_load_subclauses(
    claim: str,
) -> tuple[str, list[dict[str, str]]]:
    """Strip non-load-bearing subclauses from a load-bearing sentence and record drops."""
    cleaned = claim
    drops: list[dict[str, str]] = []
    for pattern, repl, reason in _SUBCLAUSE_TRIMS:
        match = pattern.search(cleaned)
        if match:
            dropped_span = (
                match.group(2).strip()
                if match.lastindex and match.lastindex >= 2
                else match.group(0).strip(" ,;")
            )
            drops.append({"claim": dropped_span, "reason": reason})
            cleaned = pattern.sub(repl, cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    if cleaned and cleaned[0].islower():
        cleaned = cleaned[0].upper() + cleaned[1:]
    return cleaned, drops


def classify_claim_load_bearing(
    claim: str,
    *,
    purpose: str,
    audience: str,
) -> tuple[bool, str | None]:
    """Return (load_bearing, reason_if_dropped)."""
    del purpose, audience
    for pattern, reason in _NON_LOAD_PATTERNS:
        if pattern.search(claim):
            return False, reason
    return True, None


def build_fact_inventory(
    prior_draft: str,
    *,
    purpose: str,
    audience: str,
    attested_sources: list[str] | None = None,
    verbatim_notes: list[str] | None = None,
) -> dict[str, Any]:
    """Classify claims and produce load-bearing inventory + dropped_facts with provenance counts."""
    attested_sources = list(attested_sources or [])
    verbatim_notes = list(verbatim_notes or [])
    checked_body = _strip_non_sworn_body(prior_draft)
    candidates = extract_candidate_claims(prior_draft)
    load_bearing: list[str] = []
    dropped_facts: list[dict[str, str]] = []
    for claim in candidates:
        trimmed_claim, sub_drops = _trim_non_load_subclauses(claim)
        lb, reason = classify_claim_load_bearing(
            trimmed_claim, purpose=purpose, audience=audience
        )
        if lb:
            dropped_facts.extend(sub_drops)
            load_bearing.append(trimmed_claim)
        elif reason:
            dropped_facts.append({"claim": claim, "reason": reason})
    if not verbatim_notes and load_bearing:
        verbatim_notes = list(load_bearing)
    attested = list(dict.fromkeys([*verbatim_notes, *attested_sources, *load_bearing]))
    return {
        "input_sha256": hashlib.sha256(prior_draft.encode("utf-8")).hexdigest(),
        "checked_sha256": hashlib.sha256(checked_body.encode("utf-8")).hexdigest(),
        "input_word_count": len(prior_draft.split()),
        "sworn_body_word_count": _measure_sworn_body_words(prior_draft),
        "checked_word_count": len(checked_body.split()),
        "sentence_count": len(candidates),
        "load_bearing_claims": load_bearing,
        "dropped_facts": dropped_facts,
        "verbatim_notes": verbatim_notes,
        "attested_sources": attested,
    }


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
    (
        re.compile(
            r"stamp reads|RECEIVED\s+(?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\s+\d|"
            r"which the Clerk stamped|Date Filed:\s*\d|bill-created date|\b\d+\s+days after|"
            r"side office in the building|orally or in writing|envelope it came in|MANSUBI KAYWAN|"
            r"At \d{1,2}:\d{2}\s+[AP]M that day, I emailed",
            re.I,
        ),
        "clerical_recital",
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
            for sent in _split_sentences(joined):
                sent = sent.strip()
                if len(sent) >= 12:
                    units.append(sent)
        paragraph_lines = []

    for line in text.splitlines():
        if past_valediction:
            break
        stripped = line.strip()
        if _HEADER_LINE.match(line):
            continue
        if stripped and len(stripped) <= 80 and _SALUTATION.match(stripped):
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


def drift_check_sentence(
    sentence: str,
    *,
    channel: str,
    exhibit_ids: set[str],
    purpose_points: Sequence[str] | None = None,
    verbatim_required_excerpts: Sequence[str] = (),
) -> str | None:
    """Return drift reason if sentence should be flagged."""
    for pattern, reason in _DRIFT_PATTERNS:
        if pattern.search(sentence):
            return reason
    if check_class_for_channel(channel) == "correspondence" and is_declaration_register(
        sentence
    ):
        exempt = any(sentence in str(excerpt) for excerpt in verbatim_required_excerpts)
        if not exempt:
            return "declaration_register_in_correspondence"
    if channel == "filing" and exhibit_ids:
        if _EXHIBIT_LEDGER.search(sentence) and not _EXHIBIT_TAG.search(sentence):
            return "exhibit_fact_missing_inline_tag"
    if purpose_points and map_claim_to_purpose(sentence, purpose_points) is None:
        return "not_purpose_relevant"
    return None


def run_drift_check(
    draft_text: str,
    *,
    channel: str,
    pin_ledger: list[dict[str, Any]] | None = None,
    strip_non_sworn: bool = True,
    purpose_points: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Sentence-level drift check; branches on channel — correspondence has no sworn fields."""
    check_class = check_class_for_channel(channel)
    points = [p for p in (purpose_points or []) if str(p).strip()]
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
        "purpose_gate": "applied" if points else "skipped_no_purpose_points",
    }
    if check_class == "sworn":
        result["sworn_body_word_count"] = _measure_sworn_body_words(draft_text)
    return result
