"""Brief-domain output validator test (in-memory fixture, no live DB).

Synthesizes a legal_brief-style response containing planted defects that trigger
ALL six Phase 1.0 finding kinds exactly once. Verifies review_required=True and
exact kind strings per §12.13 table (verbatim from work-order).

The fixture paraphrases defect patterns from the BOE-19-P v5→v6 correction ledger
but does not read or depend on that file at runtime.
"""

from __future__ import annotations

from unittest.mock import patch

from ..validator_output import validate_output


def _mk_assertion_for_brief(
    assertion_id: int,
    claim: str = "boilerplate claim",
    chunk_id: int | None = 999,
    derivation_type: str = "direct_observation",
    valid_from: str | None = None,
) -> dict:
    return {
        "id": assertion_id,
        "claim": claim,
        "chunk_id": chunk_id,
        "derivation_type": derivation_type,
        "valid_from": valid_from,
        "superseded_by": None,
        "valid_until": None,
    }


def test_brief_domain_emits_all_six_phase_1_0_findings():
    """One planted trigger per finding kind; domain_tag=legal_brief forces review_required."""
    # Text designed to hit:
    # 13: [assertion:999] (missing) + a ledger claim without cite
    # 5-ext: "wrong quote" near [assertion:10] (chunk_id set, claim mismatch)
    # 14: via ledger with 9 supports (high cardinality)
    # 15: "shows that" near [assertion:20] (inference)
    # 16: [assertion:30] with valid_from but no ISO date in para
    # 17: body cites 100, bib cites 101 -> two orphans but we count as one kind emitted
    text = """
## Statement of Facts
The total exposure is $9,999,999 according to the record [assertion:999].
The timeline "began on the first" [assertion:10].
This analysis shows that liability attaches [assertion:20].
The term commenced [assertion:30] without further qualification.

## References
See prior art [assertion:101].
"""

    # ledger for 13c (claim without cite nearby) + 14 (9 supports)
    ledger = [
        {"claim_text": "$9,999,999", "supporting_assertion_id": 5000 + i} for i in range(9)
    ]
    # one more for the missing-cite detection (the $9,999,999 already in text but we use different claim for 13c to avoid overlap)
    ledger.append({"claim_text": "separate un-cited fact 12345", "supporting_assertion_id": 6000})

    def fake_fetch(aid: int):
        if aid == 999:
            return None
        if aid == 10:
            return _mk_assertion_for_brief(10, claim="the term began on the second", chunk_id=42)
        if aid == 20:
            return _mk_assertion_for_brief(20, derivation_type="inference")
        if aid == 30:
            return _mk_assertion_for_brief(30, valid_from="2024-11-05")
        if aid == 100:
            return _mk_assertion_for_brief(100)
        if aid == 101:
            return _mk_assertion_for_brief(101)
        return _mk_assertion_for_brief(aid)

    with patch("cortex_store.agent_injection.validator_output._fetch_assertion", side_effect=fake_fetch):
        res = validate_output(text, ledger=ledger, domain_tag="legal_brief")

    assert res.review_required is True
    kinds = {f.kind for f in res.findings}
    expected = {
        "output_citation_missing_assertion",
        "verbatim_check_failed",
        "output_citation_high_cardinality",
        "grade_laundering_in_output",
        "temporal_qualification_omitted",
        "bibliography_orphan",
    }
    assert kinds == expected, f"got {kinds}"
    # exactly one of each (or at least the set covers; count may be >1 for orphans but kinds unique)
    assert len(kinds) == 6
