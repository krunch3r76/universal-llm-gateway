"""8 Q1 canonical fixtures — v4 §10.6 acceptance gate.

Each fixture pins the legacy stored form and the expected canonical
output of `normalize_predicate_domain()`. Sourced from the cursor
dispatch packet (`v1.3-normalize-predicate-domain-cursor-dispatch.md`)
and the v4 §10.6 CM-Select-with-patch verification table.

Pre-flight SQL on these 8 IDs (3623, 4134, 3284, 4135, 3818, 5697,
3557, 4525) executed in session claude-web-2026-05-16-1911 confirmed
all are active (no superseded_by, no valid_until) at packet time.

Q1 (4525 third arg) is ratified as option (c) — exact-match-only;
the bare token `estate_of_dr_fred_mansubi` has no exact match and
remains bare in the expected output.
"""

from __future__ import annotations

import pytest
from predicate_form import StaticEntityResolver, normalize_predicate_domain

# Slug → entity_id map covering all entities referenced by the 8 fixtures.
# The resolver is exact-match-only against this set.
_RESOLVER = StaticEntityResolver(
    {
        "camelia-mahmoudi": "person:camelia-mahmoudi",
        "kaywan-mansubi": "person:kaywan-mansubi",
        "affidavit-of-death-community-property-owner": (
            "legal_matter:affidavit-of-death-community-property-owner"
        ),
        "mary-mansubi-life-insurance-policy-500k": (
            "asset:mary-mansubi-life-insurance-policy-500k"
        ),
        "mary-mansubi-life-insurance-policy-200k": (
            "asset:mary-mansubi-life-insurance-policy-200k"
        ),
    }
)


Q1_FIXTURES = [
    pytest.param(
        3284,
        "person:camelia-mahmoudi",
        "role(camelia_mahmoudi, filer, 24PR197054)",
        "role(person:camelia-mahmoudi, filer, 24pr197054)",
        {2, 3},
        id="3284_role_caseid",
    ),
    pytest.param(
        3557,
        "person:camelia-mahmoudi",
        "role(camelia_mahmoudi, filer, case)",
        "role(person:camelia-mahmoudi, filer, case)",
        {2},
        id="3557_role_case_token",
    ),
    pytest.param(
        3623,
        "legal_matter:affidavit-of-death-community-property-owner",
        "has_attribute(affidavit_of_death_community_property_owner, cost, 450)",
        "has_attribute(legal_matter:affidavit-of-death-community-property-owner, cost, 450)",
        {2},
        id="3623_has_attribute_cost",
    ),
    pytest.param(
        3818,
        "legal_matter:estate-of-fred-mansubi-24pr197054",
        "status(camelia_mahmoudi, ready_to_file)",
        "status(person:camelia-mahmoudi, ready_to_file)",
        {2},
        id="3818_status_ready_to_file_subject_swap",
    ),
    pytest.param(
        4134,
        "asset:mary-mansubi-life-insurance-policy-500k",
        "has_attribute(mary_mansubi_life_insurance_policy_500k, value_500000)",
        "has_attribute(asset:mary-mansubi-life-insurance-policy-500k, value_500000)",
        {2},
        id="4134_has_attribute_500k",
    ),
    pytest.param(
        4135,
        "asset:mary-mansubi-life-insurance-policy-200k",
        "has_attribute(mary_mansubi_life_insurance_policy_200k, value_200000)",
        "has_attribute(asset:mary-mansubi-life-insurance-policy-200k, value_200000)",
        {2},
        id="4135_has_attribute_200k",
    ),
    pytest.param(
        4525,
        "person:kaywan-mansubi",
        "role(kaywan_mansubi, administrator, estate_of_dr_fred_mansubi)",
        "role(person:kaywan-mansubi, administrator, estate_of_dr_fred_mansubi)",
        {2},
        id="4525_role_admin_q1_partial",
    ),
    pytest.param(
        5697,
        "person:camelia-mahmoudi",
        "status(camelia_mahmoudi, unavailable, August_21_2024)",
        "status(person:camelia-mahmoudi, unavailable, august_21_2024)",
        {2, 3},
        id="5697_status_august_date_fold",
    ),
]


@pytest.mark.parametrize(
    "assertion_id,entity_id,legacy,expected_canonical,expected_classes",
    Q1_FIXTURES,
)
def test_q1_canonical(
    assertion_id: int,
    entity_id: str,
    legacy: str,
    expected_canonical: str,
    expected_classes: set[int],
) -> None:
    out = normalize_predicate_domain(
        entity_id,
        legacy,
        claim_text=None,
        resolver=_RESOLVER,
    )
    assert out["canonical_form"] == expected_canonical, (
        f"id={assertion_id} canonical_form mismatch"
    )
    assert set(out["classes_applied"]) == expected_classes, (
        f"id={assertion_id} classes_applied={out['classes_applied']!r}"
    )
    assert out["requires_human_review"] is False


def test_agm_uniformity_equivalent_inputs_same_domain_key() -> None:
    """K*7/K*8 — equivalent legacy forms produce identical domain_key."""
    cases = [
        "role(camelia_mahmoudi, filer, 24PR197054)",
        "role(camelia_mahmoudi, filer, case_24PR197054)",
        "role(camelia_mahmoudi, filer, 24pr197054)",
    ]
    keys = {
        normalize_predicate_domain("person:camelia-mahmoudi", c, resolver=_RESOLVER)[
            "domain_key"
        ]
        for c in cases
    }
    assert len(keys) == 1, f"non-uniform domain_keys: {keys}"


def test_domain_key_is_bare_form() -> None:
    out = normalize_predicate_domain(
        "person:camelia-mahmoudi",
        "status(camelia_mahmoudi, unavailable, August_21_2024)",
        resolver=_RESOLVER,
    )
    assert out["domain_key"] == (
        "status(camelia_mahmoudi, unavailable, august_21_2024)"
    )
    assert out["canonical_form"] == (
        "status(person:camelia-mahmoudi, unavailable, august_21_2024)"
    )
