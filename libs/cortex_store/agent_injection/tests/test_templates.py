"""Unit tests for templates.py — must exercise exact canonical bracket tokens."""

from __future__ import annotations

import pytest

from ..errors import TemplateRenderError
from ..templates import (
    D1_TEMPLATE,
    D2_TEMPLATE,
    D3_TEMPLATE,
    D4_TEMPLATE,
    render_d1,
    render_d2,
    render_d3,
    render_d4,
)


def test_d1_template_markers():
    assert "[STRUCTURED_LOOKUP" in D1_TEMPLATE
    assert "[/STRUCTURED_LOOKUP]" in D1_TEMPLATE


def test_d2_template_markers():
    assert "[CONTEXT_PROVISION" in D2_TEMPLATE
    assert "[/CONTEXT_PROVISION]" in D2_TEMPLATE


def test_d3_template_markers():
    assert "[TEMPORAL_QUALIFIED" in D3_TEMPLATE
    assert "[/TEMPORAL_QUALIFIED]" in D3_TEMPLATE


def test_d4_template_markers():
    assert "[BELIEF_INJECTION" in D4_TEMPLATE
    assert "[/BELIEF_INJECTION]" in D4_TEMPLATE


def test_render_d1_happy():
    ctx = {
        "assertion_id": 42,
        "confidence_score": 0.95,
        "valid_from": "2025-01-01",
        "utc_now": "2026-05-17T12:00:00",
        "field_name": "path",
        "claim_value": "/tmp/foo",
    }
    out = render_d1(ctx)
    assert "[STRUCTURED_LOOKUP | source: assertion 42" in out
    assert "Value: /tmp/foo" in out
    assert "[/STRUCTURED_LOOKUP]" in out


def test_render_d1_missing_key_raises():
    with pytest.raises(TemplateRenderError) as exc:
        render_d1({"assertion_id": 1})  # missing many
    assert "D.1 missing key" in str(exc.value)


def test_render_d2_happy():
    ctx = {
        "entity_id": "case:123",
        "included_count": 3,
        "total_active_count": 10,
        "truncated": "false",
        "selection_strategy": "all",
        "selection_params": "none",
        "pulled_at": "2026-05-17T12:00:00",
        "cursor": "none",
        "content_hash": "sha256:deadbeef",
        "rows_block": "  assertion_id=1 predicate=owns claim=foo",
    }
    out = render_d2(ctx)
    assert "[CONTEXT_PROVISION" in out
    assert "content_hash: sha256:deadbeef" in out
    assert "[/CONTEXT_PROVISION]" in out


def test_render_d2_missing_key_raises():
    with pytest.raises(TemplateRenderError) as exc:
        render_d2({"entity_id": "x"})  # incomplete
    assert "D.2 missing key" in str(exc.value)


def test_render_d3_happy():
    ctx = {
        "assertion_id": 99,
        "valid_from": "2025-01-01",
        "valid_until": "2026-01-01",
        "utc_now": "2026-05-17",
        "freshness": "CURRENT",
        "claim": "bar",
    }
    out = render_d3(ctx)
    assert "[TEMPORAL_QUALIFIED | assertion_id: 99" in out
    assert "freshness: CURRENT" in out
    assert "Claim: bar" in out


def test_render_d3_missing_raises():
    with pytest.raises(TemplateRenderError) as exc:
        render_d3({})
    assert "D.3 missing key" in str(exc.value)


def test_render_d4_happy():
    ctx = {
        "assertion_id": 7,
        "confidence_score": 0.4,
        "derivation_type": "inference",
        "seeded_by": "agent",
        "seeded_at": "2025-01-01",
        "claim": "hypo",
        "reasoning_summary": "because",
    }
    out = render_d4(ctx)
    assert "[BELIEF_INJECTION | assertion_id: 7" in out
    assert "Reasoning: because" in out


def test_render_d4_missing_raises():
    with pytest.raises(TemplateRenderError) as exc:
        render_d4({"assertion_id": 1})
    assert "D.4 missing key" in str(exc.value)


def test_no_placeholder_in_source():
    """Anti-fabrication gate: source must not contain v1-style placeholder text."""
    import os

    here = os.path.dirname(__file__)
    src_path = os.path.join(here, "..", "templates.py")
    with open(src_path) as f:
        source = f.read()
    assert "placeholder" not in source.lower()
    assert "pending insertion" not in source.lower()
