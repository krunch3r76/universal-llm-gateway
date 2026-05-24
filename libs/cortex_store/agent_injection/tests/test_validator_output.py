"""Unit tests for validator_output.py — per-finding-kind coverage for the six Phase 1.0 kinds,
review_required signals, and load-bearing `response_text` parameter name check."""

from __future__ import annotations

import inspect
from unittest.mock import patch

from ..validator_output import (
    OutputValidationResult,
    validate_output,
)


def _mk_assertion(
    assertion_id: int = 42,
    claim: str = "the sky is blue",
    chunk_id: int | None = None,
    derivation_type: str = "direct_observation",
    valid_from: str | None = None,
    superseded_by: int | None = None,
    valid_until: str | None = None,
) -> dict:
    return {
        "id": assertion_id,
        "claim": claim,
        "chunk_id": chunk_id,
        "derivation_type": derivation_type,
        "valid_from": valid_from,
        "superseded_by": superseded_by,
        "valid_until": valid_until,
    }


def test_finding_13_missing_assertion():
    # Plant a [assertion:99999] ; mock fetch to return None.
    text = "The event happened [assertion:99999] on Tuesday."
    with patch(
        "cortex_store.agent_injection.validator_output._fetch_assertion",
        return_value=None,
    ):
        res = validate_output(text)
    assert isinstance(res, OutputValidationResult)
    assert len(res.findings) == 1
    f = res.findings[0]
    assert f.kind == "output_citation_missing_assertion"
    assert f.severity == "high"
    assert f.evidence.get("reason") == "not_found"
    assert f.location and f.location["citation_id"] == 99999


def test_finding_13_retired_superseded():
    text = "Per the record [assertion:123]."
    retired = _mk_assertion(123, superseded_by=456, valid_until="2025-01-01")
    with patch(
        "cortex_store.agent_injection.validator_output._fetch_assertion",
        return_value=retired,
    ):
        res = validate_output(text)
    assert len(res.findings) == 1
    assert res.findings[0].kind == "output_citation_missing_assertion"
    assert res.findings[0].evidence.get("reason") == "retired"


def test_finding_13_ledger_claim_without_citation():
    # ledger claim appears in text but no [assertion: ] within ±20
    text = "The total damages are $1,234,567. This is the key fact."
    ledger = [{"claim_text": "$1,234,567", "supporting_assertion_id": 77}]
    with patch(
        "cortex_store.agent_injection.validator_output._fetch_assertion",
        return_value=None,
    ):
        res = validate_output(text, ledger=ledger)
    missing = [
        f
        for f in res.findings
        if f.kind == "output_citation_missing_assertion"
        and f.evidence.get("reason") == "ledger_claim_without_citation"
    ]
    assert len(missing) >= 1


def test_finding_5_ext_verbatim_check_failed():
    text = 'The contract states "the sky is green" [assertion:42].'
    assertion = _mk_assertion(42, claim="the sky is blue", chunk_id=101)
    with patch(
        "cortex_store.agent_injection.validator_output._fetch_assertion",
        return_value=assertion,
    ):
        res = validate_output(text)
    vf = [f for f in res.findings if f.kind == "verbatim_check_failed"]
    assert len(vf) == 1
    assert vf[0].severity == "high"
    assert vf[0].evidence.get("extension") == "output"
    assert "green" in vf[0].evidence.get("quoted", "")


def test_finding_14_high_cardinality():
    # 9 supporting for one claim
    ledger = [
        {"claim_text": "X caused Y", "supporting_assertion_id": i}
        for i in range(10, 19)
    ]
    res = validate_output(
        "irrelevant text here", ledger=ledger, high_cardinality_threshold=8
    )
    hc = [f for f in res.findings if f.kind == "output_citation_high_cardinality"]
    assert len(hc) == 1
    assert hc[0].severity == "medium"
    assert hc[0].evidence["supporting_count"] == 9


def test_finding_15_grade_laundering():
    text = "The analysis shows that the defendant is liable [assertion:55]."
    inf = _mk_assertion(55, derivation_type="inference")
    with patch(
        "cortex_store.agent_injection.validator_output._fetch_assertion",
        return_value=inf,
    ):
        res = validate_output(text)
    gl = [f for f in res.findings if f.kind == "grade_laundering_in_output"]
    assert len(gl) == 1
    assert gl[0].severity == "high"
    assert gl[0].evidence["derivation_type"] == "inference"


def test_finding_16_temporal_omitted():
    text = "The policy took effect [assertion:88] and coverage began."
    # no ISO date in surrounding para
    timed = _mk_assertion(88, valid_from="2025-03-01")
    with patch(
        "cortex_store.agent_injection.validator_output._fetch_assertion",
        return_value=timed,
    ):
        res = validate_output(text)
    to = [f for f in res.findings if f.kind == "temporal_qualification_omitted"]
    assert len(to) == 1
    assert to[0].severity == "medium"


def test_finding_17_bibliography_orphan():
    text = """## Summary
The key fact is here [assertion:1].

## References
- See also [assertion:2]
"""
    with patch(
        "cortex_store.agent_injection.validator_output._fetch_assertion",
        return_value=_mk_assertion(1),
    ):
        res = validate_output(text, domain_tag="legal_brief")
    orphans = [f for f in res.findings if f.kind == "bibliography_orphan"]
    # body has 1, bib has 2 -> two orphans
    assert len(orphans) == 2
    kinds = {o.evidence.get("location") for o in orphans}
    assert "body_not_in_bib" in kinds
    assert "bib_not_in_body" in kinds


def test_review_required_brief_domain():
    # clean response, no findings, but domain_tag -> True
    text = "All good [assertion:1]."
    assertion = _mk_assertion(1)
    with patch(
        "cortex_store.agent_injection.validator_output._fetch_assertion",
        return_value=assertion,
    ):
        res = validate_output(text, domain_tag="demand_letter")
    assert res.review_required is True
    assert res.ok is True  # no findings
    assert res.findings == []


def test_review_required_high_severity():
    text = "Bad [assertion:99999]."
    with patch(
        "cortex_store.agent_injection.validator_output._fetch_assertion",
        return_value=None,
    ):
        res = validate_output(text, domain_tag=None)
    assert res.review_required is True
    assert any(f.severity == "high" for f in res.findings)


def test_validate_output_parameter_name():
    """Source-grep style check: the parameter is literally named `response_text` (load-bearing)."""
    sig = inspect.signature(validate_output)
    assert "response_text" in sig.parameters
    # also verify source contains the token (per work-order intent)
    import pathlib

    src = pathlib.Path(__file__).parent.parent / "validator_output.py"
    content = src.read_text(encoding="utf-8")
    assert "def validate_output(" in content
    assert "response_text: str" in content
