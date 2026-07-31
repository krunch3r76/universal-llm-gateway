"""Unit tests for scripts/cortex/_skill_terms derivation helpers (4696 F2)."""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS_CORTEX = Path(__file__).resolve().parent
_REPO = _SCRIPTS_CORTEX.parent.parent
if str(_SCRIPTS_CORTEX) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_CORTEX))
if str(_REPO / "libs") not in sys.path:
    sys.path.insert(0, str(_REPO / "libs"))

from _skill_terms import (  # noqa: E402
    _MAX_TERMS,
    _keep_term,
    canonicalize_trigger_match_terms,
    derive_projection_trigger_match_terms,
    derive_trigger_match_terms,
    derive_trigger_match_terms_from_vocab,
)


def test_canonicalize_dedupes_case_and_sorts() -> None:
    assert canonicalize_trigger_match_terms(
        ["Beta", "alpha", "BETA", "alpha", "Gamma"]
    ) == ["alpha", "Beta", "Gamma"]


def test_keep_term_drops_short_and_procedural_unless_domain() -> None:
    domain = {"when", "dispatch"}
    assert _keep_term("ab", domain_tokens=domain) is False
    assert _keep_term("when", domain_tokens=set()) is False
    assert _keep_term("when", domain_tokens=domain) is True
    assert _keep_term("dispatch", domain_tokens=domain) is True


def test_derive_trigger_match_terms_includes_slug_variants_and_caps() -> None:
    terms = derive_trigger_match_terms(
        "dispatch-shape",
        trigger_short="before handoff ∨ when packet",
        skill_category="workflow",
        description="Load when authoring handoff packets for dispatch.",
    )
    assert "dispatch-shape" in terms
    assert "dispatch_shape" in terms
    assert "handoff" in terms or "packet" in terms
    assert len(terms) <= _MAX_TERMS
    # FOL operators stripped — not emitted as terms
    assert "∨" not in terms


def test_derive_trigger_match_terms_from_vocab_top_n_by_score() -> None:
    rows = [
        ("other", "domain", "noise", 99.0, 1),
        ("s", "domain", "zeta", 5.0, 1),
        ("s", "domain", "alpha", 9.0, 2),
        ("s", "domain", "Alpha", 8.0, 1),  # case-dupe of alpha
        ("s", "domain", "beta", 7.0, 1),
    ]
    assert derive_trigger_match_terms_from_vocab("s", vocab_rows=rows, top_n=2) == [
        "alpha",
        "beta",
    ]


def test_derive_projection_prefers_nonempty_vocab_over_description() -> None:
    vocab = [
        ("proj-skill", "domain", "vocab-term", 10.0, 1),
    ]
    terms = derive_projection_trigger_match_terms(
        "proj-skill",
        frontmatter={"trigger_short": "ignored-when-vocab"},
        description="description-only-term-should-not-win",
        vocab_rows=vocab,
    )
    assert terms == ["vocab-term"]


def test_derive_projection_falls_back_to_description_when_vocab_empty() -> None:
    terms = derive_projection_trigger_match_terms(
        "proj-skill",
        frontmatter={"skill_category": "tooling"},
        description="Load when implementing cortex ingest workflows.",
        vocab_rows=[],
    )
    assert "proj-skill" in terms or "proj_skill" in terms
    assert terms == canonicalize_trigger_match_terms(terms)
