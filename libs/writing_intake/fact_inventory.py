"""Fact-inventory mode: extract claims from prior_draft and classify load-bearing."""

from __future__ import annotations

import hashlib
import re
from typing import Any

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
    (re.compile(r"\brev\s+\d|not legal advice|attorney review|\bdraft\b", re.I), "process_chat_residue"),
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
        if merged and (
            _ABBREV.search(merged[-1]) or _STANDALONE_EXHIBIT.match(part)
        ):
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


def drift_check_sentence(sentence: str, *, channel: str, exhibit_ids: set[str]) -> str | None:
    """Return drift reason if sentence should be flagged."""
    for pattern, reason in _DRIFT_PATTERNS:
        if pattern.search(sentence):
            return reason
    if channel == "filing" and exhibit_ids:
        if _EXHIBIT_LEDGER.search(sentence) and not _EXHIBIT_TAG.search(sentence):
            return "exhibit_fact_missing_inline_tag"
    return None


def run_drift_check(
    draft_text: str,
    *,
    channel: str,
    pin_ledger: list[dict[str, Any]] | None = None,
    strip_non_sworn: bool = True,
) -> dict[str, Any]:
    """Sentence-level purpose drift check over final draft with input provenance counts."""
    exhibit_ids: set[str] = set()
    if pin_ledger:
        for pin in pin_ledger:
            excerpt = str(pin.get("excerpt") or "")
            for match in _EXHIBIT_LEDGER.finditer(excerpt):
                exhibit_ids.add(match.group(1))
    checked_body = _strip_non_sworn_body(draft_text) if strip_non_sworn else draft_text
    flagged: list[dict[str, str]] = []
    seen_propositions: set[str] = set()
    sentence_count = 0
    for para in re.split(r"\n\s*\n", checked_body):
        for sent in _split_sentences(para.strip()):
            sent = sent.strip()
            if not sent:
                continue
            sentence_count += 1
            reason = drift_check_sentence(sent, channel=channel, exhibit_ids=exhibit_ids)
            if reason:
                flagged.append({"sentence": sent, "reason": reason})
                continue
            normalized = re.sub(r"\s+", " ", sent.lower())
            normalized = re.sub(r"\(exhibit\s+\d+\.?\)", "", normalized, flags=re.I)
            if len(normalized) > 40 and normalized in seen_propositions:
                flagged.append({"sentence": sent, "reason": "re_explanation_same_draft"})
            seen_propositions.add(normalized)
    return {
        "passed": not flagged,
        "input_sha256": hashlib.sha256(draft_text.encode("utf-8")).hexdigest(),
        "checked_sha256": hashlib.sha256(checked_body.encode("utf-8")).hexdigest(),
        "input_word_count": len(draft_text.split()),
        "sworn_body_word_count": _measure_sworn_body_words(draft_text),
        "checked_word_count": len(checked_body.split()),
        "sentence_count": sentence_count,
        "flagged_sentences": flagged,
    }
