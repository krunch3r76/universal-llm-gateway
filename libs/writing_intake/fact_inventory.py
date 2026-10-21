"""Fact-inventory mode: extract claims from prior_draft and classify load-bearing."""

from __future__ import annotations

import re
from typing import Any

_SENTENCE = re.compile(r"(?<=[.!?])\s+")
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


def extract_candidate_claims(prior_draft: str) -> list[str]:
    """Split prior_draft into candidate factual sentences."""
    body = _strip_non_sworn_body(prior_draft)
    chunks: list[str] = []
    for para in re.split(r"\n\s*\n", body):
        para = para.strip()
        if not para or para.startswith("#"):
            continue
        for sent in _SENTENCE.split(para):
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
    """Classify claims and produce load-bearing inventory + dropped_facts."""
    attested_sources = list(attested_sources or [])
    verbatim_notes = list(verbatim_notes or [])
    candidates = extract_candidate_claims(prior_draft)
    load_bearing: list[str] = []
    dropped_facts: list[dict[str, str]] = []
    for claim in candidates:
        lb, reason = classify_claim_load_bearing(
            claim, purpose=purpose, audience=audience
        )
        if lb:
            load_bearing.append(claim)
        elif reason:
            dropped_facts.append({"claim": claim, "reason": reason})
    if not verbatim_notes and load_bearing:
        verbatim_notes = list(load_bearing)
    attested = list(dict.fromkeys([*verbatim_notes, *attested_sources, *load_bearing]))
    return {
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
) -> dict[str, Any]:
    """Sentence-level purpose drift check over final draft."""
    exhibit_ids: set[str] = set()
    if pin_ledger:
        for pin in pin_ledger:
            excerpt = str(pin.get("excerpt") or "")
            for match in _EXHIBIT_LEDGER.finditer(excerpt):
                exhibit_ids.add(match.group(1))
    flagged: list[dict[str, str]] = []
    seen_propositions: set[str] = set()
    for para in re.split(r"\n\s*\n", draft_text):
        for sent in _SENTENCE.split(para.strip()):
            sent = sent.strip()
            if not sent:
                continue
            reason = drift_check_sentence(sent, channel=channel, exhibit_ids=exhibit_ids)
            if reason:
                flagged.append({"sentence": sent, "reason": reason})
                continue
            normalized = re.sub(r"\s+", " ", sent.lower())
            normalized = re.sub(r"\(exhibit\s+\d+\)", "", normalized, flags=re.I)
            if len(normalized) > 40 and normalized in seen_propositions:
                flagged.append({"sentence": sent, "reason": "re_explanation_same_draft"})
            seen_propositions.add(normalized)
    return {"passed": not flagged, "flagged_sentences": flagged}
