"""Unit tests for validator_preflight.py — one + / one - per D.5 invariant, parameter name check."""

from __future__ import annotations

import os
import re

import pytest

from ..errors import AgentInjectionAdmissionError
from ..materializers import compute_d2_content_hash
from ..validator_preflight import ValidationResult, preflight_validate


def _d1_block(assertion_id: int = 1, claim: str = "x") -> dict:
    rendered = f"[STRUCTURED_LOOKUP | source: assertion {assertion_id} | confidence: 0.9 | valid_from: 2025 | checked: 2026]\nField: f\nValue: {claim}\n[/STRUCTURED_LOOKUP]\n"
    return {
        "kind": "d1",
        "rendered": rendered,
        "assertion_id": assertion_id,
        "grade": "structural",
    }


def _d2_block(
    truncated: bool = False,
    cursor: str | None = None,
    strategy: str = "all",
    content_hash: str | None = None,
) -> dict:
    rows = "  assertion_id=42 predicate=p claim=c confidence=0.5 valid_from=2025"
    base = f"""[CONTEXT_PROVISION
  | entity: e:1
  | included_count: 1
  | total_active_count: 1
  | truncated: {"true" if truncated else "false"}
  | selection_strategy: {strategy}
  | selection_params: none
  | pulled_at: 2026
  | cursor: {cursor or "none"}
  | content_hash: PLACEHOLDER
]
{rows}
[/CONTEXT_PROVISION]
"""
    if content_hash is None:
        # compute proper
        wo = base.replace("  | content_hash: PLACEHOLDER\n", "")
        content_hash = compute_d2_content_hash(wo)
    rendered = base.replace("PLACEHOLDER", content_hash)
    return {
        "kind": "d2",
        "rendered": rendered,
        "entity_id": "e:1",
        "included_count": 1,
        "total_active_count": 1,
        "truncated": truncated,
        "cursor": cursor,
        "selection_strategy": strategy,
        "content_hash": content_hash,
        "grade": "structural",
    }


def test_preflight_clean_packet_passes():
    pkt = [_d1_block(), _d2_block()]
    res = preflight_validate(pkt)
    assert isinstance(res, ValidationResult)
    assert res.ok is True
    assert res.block_count == 2


def test_preflight_invariant_1_envelope_not_first():
    bad = _d1_block()
    bad["rendered"] = "some prose first\n" + bad["rendered"]
    with pytest.raises(AgentInjectionAdmissionError) as exc:
        preflight_validate([bad])
    assert exc.value.violations[0].invariant == 1
    assert "envelope_not_first" in exc.value.violations[0].detail


def test_preflight_invariant_2_missing_citation():
    bad = _d1_block()
    # corrupt both the dict key and the rendered patterns (D1 uses "source: assertion NNN")
    bad["assertion_id"] = None
    bad["rendered"] = re.sub(
        r"source: assertion \d+", "source: assertion ???", bad["rendered"]
    )
    bad["rendered"] = bad["rendered"].replace("assertion_id=1", "no_id_here")
    with pytest.raises(AgentInjectionAdmissionError) as exc:
        preflight_validate([bad])
    assert exc.value.violations[0].invariant == 2
    assert "missing_citation_anchor" in exc.value.violations[0].detail


def test_preflight_invariant_3_prose_laundering():
    bad = _d1_block()
    # insert a sentence line after meta
    bad["rendered"] = bad["rendered"].replace(
        "Value: x", "Value: x\nThis is a sentence that should not be here."
    )
    with pytest.raises(AgentInjectionAdmissionError) as exc:
        preflight_validate([bad])
    assert exc.value.violations[0].invariant == 3


def test_preflight_invariant_4_truncation_violation():
    bad = _d2_block(truncated=True, cursor=None, strategy="all")
    with pytest.raises(AgentInjectionAdmissionError) as exc:
        preflight_validate([bad])
    assert exc.value.violations[0].invariant == 4


def test_preflight_invariant_5_hash_mismatch():
    bad = _d2_block()
    bad["content_hash"] = "sha256:" + "0" * 64  # wrong value
    with pytest.raises(AgentInjectionAdmissionError) as exc:
        preflight_validate([bad])
    assert exc.value.violations[0].invariant == 5
    assert "hash_mismatch" in exc.value.violations[0].detail


def test_preflight_invariant_5_hash_malformed():
    bad = _d2_block()
    bad["content_hash"] = "not-a-hash"
    with pytest.raises(AgentInjectionAdmissionError) as exc:
        preflight_validate([bad])
    assert exc.value.violations[0].invariant == 5
    assert "hash_malformed" in exc.value.violations[0].detail


def test_parameter_name_is_injection_packet():
    """Source-grep anti-fabrication: confirms correct validator kind (packet vs payload)."""
    here = os.path.dirname(__file__)
    src = os.path.join(here, "..", "validator_preflight.py")
    with open(src) as f:
        text = f.read()
    assert "injection_packet" in text  # param + usage
    # count at least 2 occurrences (def + inside)
    assert text.count("injection_packet") >= 2


def test_positive_per_invariant():
    """One clean positive per invariant shape."""
    # 1-3 covered by clean packet, 4-5 by non-trunc d2 with good hash
    d2 = _d2_block(truncated=False, strategy="newest_n_by_observed_at")
    res = preflight_validate([d2])
    assert res.ok
