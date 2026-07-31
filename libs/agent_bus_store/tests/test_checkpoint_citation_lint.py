"""Unit tests for CHECKPOINT citation + namespace lint."""

from __future__ import annotations

from agent_bus_store.checkpoint_citation_lint import (
    CitationToken,
    lint_checkpoint_citations,
)

# agent-bus:6341 turn 19 — CHECKPOINT 8 (embedded excerpt; no live bus dependency)
CP8_EXCERPT = """\
- Slice A (retrospective, 3 arcs) and Slice B (forward, live consult) both complete.
  **F1 HOLDS** in both directions. Rows R1–R6 unchanged throughout.
- Capture attribution bind accepted: **declaration authoritative; observation
  corroborates** — precedence, not intersection.

| **L0** — falsifiers 1+6: declaration-coverage measurement + direct repro | `composer-2.5` | **Gates L3–L5.** Low coverage = re-bind signal, ¬ threshold to tune |

F1 **HOLDS** (retro + forward) · F2/F4/F5 untested · F3 needs a post-spec A5 recurrence.
Entities | `task:contract-envelope-abstraction` · `todo:contract-envelope-spec-v0` · `a:27033` |
"""


def test_cp8_flags_both_unqualified_usages() -> None:
    findings = lint_checkpoint_citations(CP8_EXCERPT)
    assert not findings.clean

    bare_f = [ref for ref in findings.ambiguous_refs if ref.kind == "bare_f_digit"]
    falsifier = [
        ref for ref in findings.ambiguous_refs if ref.kind == "falsifier_number"
    ]

    assert any(ref.raw == "F1" for ref in bare_f)
    assert len(bare_f) >= 2
    assert any("falsifiers 1+6" in ref.raw.lower() for ref in falsifier)


def test_qualified_forms_are_clean() -> None:
    body = (
        "envelope F1 holds under registry adjudication.\n"
        "attribution falsifier 1 is the measurement leg.\n"
        "envelope:F1 and attr:§7.1 are namespaced.\n"
    )
    findings = lint_checkpoint_citations(body)
    assert findings.clean


def test_hyphenated_slugs_never_match_bare_f_rule() -> None:
    body = "CCA-1 and CCL-0 are leg identifiers, not envelope refs."
    findings = lint_checkpoint_citations(body)
    assert findings.clean


def test_citation_extraction_all_kinds_and_dedup() -> None:
    body = (
        "bind a:27033 and todo:spec-v0; repeat a:27033; "
        "task:contract-envelope-abstraction decision:gate-leak "
        "plan:redo-probe agent-bus:6341"
    )
    findings = lint_checkpoint_citations(body)
    tokens = findings.citation_tokens

    by_kind = {token.kind: token for token in tokens}
    assert by_kind["assertion"].identifier == "27033"
    assert by_kind["todo"].identifier == "spec-v0"
    assert by_kind["task"].identifier == "contract-envelope-abstraction"
    assert by_kind["decision"].identifier == "gate-leak"
    assert by_kind["plan"].identifier == "redo-probe"
    assert by_kind["agent_bus"].identifier == "6341"
    assert len(tokens) == 6

    first_a = body.index("a:27033")
    repeat_a = body.index("a:27033", first_a + 1)
    assert tokens[0].offset == first_a
    assert repeat_a > first_a


def test_clean_body_with_no_refs() -> None:
    findings = lint_checkpoint_citations(
        "Settled: registry disposition split landed. Next: Slice A verification."
    )
    assert findings.clean
    assert findings.citation_tokens == ()


def test_word_boundary_f_digit_not_inside_tokens() -> None:
    body = "SF12X and conF1g must not trigger bare-F matches."
    findings = lint_checkpoint_citations(body)
    assert findings.clean


def test_attribution_section_qualified_falsifier() -> None:
    body = "attribution §7 falsifier 6 is discharged."
    findings = lint_checkpoint_citations(body)
    assert findings.clean


def test_citation_resolver_protocol_is_importable() -> None:
    from agent_bus_store.checkpoint_citation_lint import CitationResolver

    assert CitationResolver is not None
    _ = CitationToken(raw="a:1", kind="assertion", identifier="1", offset=0)
