"""Tax appeal action vocabulary, detector, and party binding (arc 6386 §6b)."""

from __future__ import annotations

from predicate_form.action_detection import match_claim_segments_with_reason
from predicate_form.action_enrichment import enrich_action_predicate_from_claim
from predicate_form.action_patterns import (
    _ACTION_PATTERNS_BY_DOMAIN,
    ACTION_VOCAB_BY_DOMAIN,
)
from predicate_form.action_vocabulary import (
    ACTION_VOCAB_V0,
    party_for_entity,
    party_from_entity_id,
)

_AAB_DENIAL_CLAIM = (
    "DENIAL RECEIVED — On 2026-06-05 Dr. Amber Green emailed four "
    "'Invalid – Closed' letters from the SCC Assessment Appeals Board: all four "
    "BOE-305-AH applications were closed without reinstatement."
)

_AAB_HUB = "case:boe19p-flintridge-appeal-2026"

_A20701_CLAIM = (
    "WO 956908029 / lower-payment request — DENIED, confirmed 2026-06-26. "
    "On the 2026-06-26 ~12:30 PM Chase Escalations call (case ECW260413-02188, "
    "rep 'Matthew', who reviewed Janet's notes), Chase stated it is unable to "
    "spread the escrow shortage beyond 12 months and that the request for the "
    "lower payment was DENIED."
)

_CHASE_ENTITY = "account:chase-mortgage-8787"


def test_a20699_denied_assessment_appeal_application_party_aab() -> None:
    preview = enrich_action_predicate_from_claim(
        _AAB_DENIAL_CLAIM,
        _AAB_HUB,
        assertion_id=20699,
        valid_from="2026-06-05",
        domain="tax_appeal",
    )
    assert preview is not None
    assert preview.predicate_form == (
        "denied(assessment_appeal_application, aab, 2026-06-05)"
    )
    assert preview.party == "aab"
    assert preview.party != "boe19p"
    assert preview.assertion_id == 20699


def test_slug_rule_yields_boe19p_without_domain_constant() -> None:
    assert party_from_entity_id(_AAB_HUB) == "boe19p"
    assert party_for_entity(_AAB_HUB, domain="tax_appeal") == "aab"


def test_mortgage_scope_still_works() -> None:
    preview = enrich_action_predicate_from_claim(
        _A20701_CLAIM,
        _CHASE_ENTITY,
        assertion_id=20701,
        valid_from="2026-06-26",
        domain="mortgage_escrow",
    )
    assert preview is not None
    assert preview.predicate_form == "denied(spread_extension, chase, 2026-06-26)"


def test_import_time_pattern_vocab_consistency() -> None:
    for domain, patterns in _ACTION_PATTERNS_BY_DOMAIN.items():
        derived = {action for _, action in patterns}
        assert derived == ACTION_VOCAB_BY_DOMAIN[domain]


def test_import_time_domain_disjointness() -> None:
    seen: set[str] = set()
    for actions in ACTION_VOCAB_BY_DOMAIN.values():
        assert not (seen & actions)
        seen |= actions
    assert seen == set(ACTION_VOCAB_V0)


def test_domain_kwarg_filters_tax_appeal_on_mortgage_claim() -> None:
    match, reason = match_claim_segments_with_reason(
        _A20701_CLAIM,
        domain="tax_appeal",
    )
    assert match is None
    assert reason == "detector_no_match"


def test_domain_kwarg_filters_mortgage_on_tax_appeal_claim() -> None:
    match, reason = match_claim_segments_with_reason(
        _AAB_DENIAL_CLAIM,
        domain="mortgage_escrow",
    )
    assert match is None
    assert reason == "detector_no_match"


def test_tax_appeal_vocab_contains_exactly_bound_terms() -> None:
    assert ACTION_VOCAB_BY_DOMAIN["tax_appeal"] == frozenset(
        {"assessment_appeal_application", "appeal_reinstatement"}
    )
