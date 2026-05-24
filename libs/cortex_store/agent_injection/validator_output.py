"""§12.13 output validator for Phase 1.0b — parses [assertion:NNNN] citations,
emits six §8 finding kinds, raises brief-domain review_required signal.

Finding 18 is Phase 1.5 — explicitly NOT implemented here.

The parameter `response_text` is load-bearing — distinguishes this validator
from a generic text-processing helper. The injection-time validator uses
`injection_packet`; the output-time validator uses `response_text`."""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

# NOTE: Finding 18 (output_citation_semantic_mismatch) is Phase 1.5.
# Do not implement here. Requires §14.2(b) semantic-similarity infrastructure
# + threshold tuning against the BOE-19-P v5→v6 correction ledger as labeled data.
from .materializers import _fetch_assertion


@dataclass
class Finding:
    kind: str  # one of the six Phase 1.0 finding-kind strings
    severity: str  # "high" | "medium" | "low"
    evidence: dict[str, Any] = field(default_factory=dict)
    location: dict[str, Any] | None = (
        None  # {char_offset: int, citation_id: int | None}
    )


@dataclass
class OutputValidationResult:
    ok: bool
    findings: list[Finding]
    review_required: bool


BRIEF_DOMAINS = {"legal_brief", "demand_letter", "regulatory_filing"}


def _normalize_text(s: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace for verbatim comparison."""
    s = s.lower()
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _get_surrounding_paragraph(text: str, offset: int, window: int = 200) -> str:
    """Extract paragraph around offset, split on \n\n or '. ' per §12.13 temporal rule."""
    # start: prefer last \n\n, else last '. '
    p1 = text.rfind("\n\n", 0, offset)
    p2 = text.rfind(". ", 0, offset)
    para_start = max(p1, p2)
    if para_start == -1:
        para_start = max(0, offset - window)
    else:
        para_start += (
            2
            if text[para_start : para_start + 2] == "\n\n"
            or text[para_start : para_start + 2] == ". "
            else 1
        )

    # end: next \n\n or '. '
    n1 = text.find("\n\n", offset)
    n2 = text.find(". ", offset)
    candidates = [x for x in (n1, n2) if x != -1]
    para_end = min(candidates) if candidates else min(len(text), offset + window)
    if para_end <= para_start:
        para_end = min(len(text), offset + window)
    return text[para_start:para_end]


def validate_output(
    response_text: str,
    ledger: list[dict] | None = None,
    *,
    domain_tag: str | None = None,
    high_cardinality_threshold: int = 8,
) -> OutputValidationResult:
    """Parse [assertion:NNNN] citations from rendered response text and emit §12.13 findings.

    Does NOT emit citations (consumer responsibility). Resolves via _fetch_assertion.
    """
    findings: list[Finding] = []
    text = response_text or ""

    citation_pattern = re.compile(r"\[assertion:(\d+)\]")
    citations: list[tuple[int, int]] = []
    for m in citation_pattern.finditer(text):
        citations.append((m.start(), int(m.group(1))))

    # Per-citation checks: 13(a/b), 5-ext, 15, 16
    for offset, cid in citations:
        assertion = _fetch_assertion(cid)
        loc = {"char_offset": offset, "citation_id": cid}

        if assertion is None:
            findings.append(
                Finding(
                    kind="output_citation_missing_assertion",
                    severity="high",
                    evidence={"assertion_id": cid, "reason": "not_found"},
                    location=loc,
                )
            )
            continue

        if (
            assertion.get("superseded_by") is not None
            and assertion.get("valid_until") is not None
        ):
            findings.append(
                Finding(
                    kind="output_citation_missing_assertion",
                    severity="high",
                    evidence={
                        "assertion_id": cid,
                        "reason": "retired",
                        "superseded_by": assertion.get("superseded_by"),
                    },
                    location=loc,
                )
            )
            continue

        # 5-ext: verbatim_check_failed with extension=output
        start = max(0, offset - 100)
        end = min(len(text), offset + 100)
        window = text[start:end]
        quoted = re.findall(r'"([^"]+)"', window)
        if quoted and assertion.get("chunk_id") is not None:
            claim = assertion.get("claim", "") or ""
            norm_claim = _normalize_text(claim)
            for q in quoted:
                norm_q = _normalize_text(q)
                if norm_q and norm_q != norm_claim:
                    findings.append(
                        Finding(
                            kind="verbatim_check_failed",
                            severity="high",
                            evidence={
                                "extension": "output",
                                "quoted": q,
                                "assertion_id": cid,
                                "chunk_id": assertion.get("chunk_id"),
                                "normalized_quoted": norm_q,
                                "normalized_claim": norm_claim,
                            },
                            location=loc,
                        )
                    )
                    break

        # 15: grade_laundering_in_output
        w50_start = max(0, offset - 50)
        w50_end = min(len(text), offset + 50)
        window50 = text[w50_start:w50_end]
        if assertion.get("derivation_type") in {"inference", "user_statement"}:
            if re.search(
                r"\b(is|shows|establishes|demonstrates|proves|confirms)\b",
                window50,
                re.IGNORECASE,
            ):
                findings.append(
                    Finding(
                        kind="grade_laundering_in_output",
                        severity="high",
                        evidence={
                            "assertion_id": cid,
                            "derivation_type": assertion.get("derivation_type"),
                        },
                        location=loc,
                    )
                )

        # 16: temporal_qualification_omitted
        para = _get_surrounding_paragraph(text, offset)
        if assertion.get("valid_from") and not re.search(r"\d{4}-\d{2}-\d{2}", para):
            findings.append(
                Finding(
                    kind="temporal_qualification_omitted",
                    severity="medium",
                    evidence={
                        "assertion_id": cid,
                        "valid_from": assertion.get("valid_from"),
                    },
                    location=loc,
                )
            )

    # Ledger-driven checks (13c + 14) — independent of parsed citations in body
    if ledger:
        # 14: high_cardinality
        claim_to_assertions: dict[str, set[Any]] = defaultdict(set)
        for row in ledger:
            ct = row.get("claim_text") or row.get("claim")
            sup = row.get("supporting_assertion_id") or row.get("assertion_id")
            if ct is not None and sup is not None:
                claim_to_assertions[str(ct)].add(sup)
        for ct, sups in claim_to_assertions.items():
            if len(sups) >= high_cardinality_threshold:
                findings.append(
                    Finding(
                        kind="output_citation_high_cardinality",
                        severity="medium",
                        evidence={
                            "claim_text": ct,
                            "supporting_count": len(sups),
                            "threshold": high_cardinality_threshold,
                        },
                        location=None,
                    )
                )

        # 13c: claim_text from ledger appears without nearby citation
        for row in ledger:
            ct = row.get("claim_text") or row.get("claim")
            if not ct or not isinstance(ct, str):
                continue
            idx = text.find(ct)
            while idx != -1:
                neighborhood = text[
                    max(0, idx - 20) : min(len(text), idx + len(ct) + 20)
                ]
                if "[assertion:" not in neighborhood:
                    findings.append(
                        Finding(
                            kind="output_citation_missing_assertion",
                            severity="high",
                            evidence={
                                "claim_text": ct,
                                "reason": "ledger_claim_without_citation",
                            },
                            location={"char_offset": idx, "citation_id": None},
                        )
                    )
                idx = text.find(ct, idx + 1)

    # 17: bibliography_orphan — brief-domain only
    if domain_tag in BRIEF_DOMAINS:
        bib_header = re.search(
            r"^(##+\s+)(References|Bibliography|Citations)\s*$",
            text,
            re.MULTILINE | re.IGNORECASE,
        )
        if bib_header:
            bib_start = bib_header.end()
            next_hdr = re.search(r"^##+\s+", text[bib_start:], re.MULTILINE)
            bib_end = bib_start + next_hdr.start() if next_hdr else len(text)
            bib_text = text[bib_start:bib_end]
            body_text = text[: bib_header.start()] + text[bib_end:]
            body_cites = {int(m.group(1)) for m in citation_pattern.finditer(body_text)}
            bib_cites = {int(m.group(1)) for m in citation_pattern.finditer(bib_text)}
            for c in sorted(body_cites - bib_cites):
                findings.append(
                    Finding(
                        kind="bibliography_orphan",
                        severity="high",
                        evidence={"orphan_citation": c, "location": "body_not_in_bib"},
                        location=None,
                    )
                )
            for c in sorted(bib_cites - body_cites):
                findings.append(
                    Finding(
                        kind="bibliography_orphan",
                        severity="high",
                        evidence={"orphan_citation": c, "location": "bib_not_in_body"},
                        location=None,
                    )
                )

    has_high = any(f.severity == "high" for f in findings)
    review_required = has_high or (domain_tag in BRIEF_DOMAINS)
    ok = len(findings) == 0
    return OutputValidationResult(
        ok=ok, findings=findings, review_required=review_required
    )
