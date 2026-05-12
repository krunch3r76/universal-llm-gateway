"""Tests for role_lint — see notes/system/specs/role-schema-self-concept-lint.md."""

from __future__ import annotations

import pytest

from role_lint import RoleLintError, lint_role_payload


def test_clean_payload_passes() -> None:
    """Negative case T5 — execution-contract prose accepts cleanly."""
    payload = {
        "id": "role:bard",
        "type": "role",
        "name": "Frontier Consult — Gemini",
        "description": (
            "Gemini-backed frontier consult role for cross-domain synthesis, "
            "associative reasoning, and live-web-grounded research questions "
            "where the answer benefits from broad recall over deep code-context."
        ),
        "attributes": {
            "purpose": (
                "Provide single-turn frontier consults for synthesis-heavy "
                "questions routed to the Google Gemini family."
            ),
            "allowed_models": ["google/gemini-2.5-pro"],
            "default_model": "google/gemini-2.5-pro",
            "frontier_kind": "google",
            "persona_seed_ref": "agent-identity/bard-birth.md",
        },
    }
    warnings = lint_role_payload(payload)
    assert warnings == []


def test_r1_second_person_in_purpose_rejects() -> None:
    """Positive case T2 — 'You are...' in purpose triggers R1."""
    payload = {
        "id": "role:legal-review",
        "name": "Legal Review",
        "attributes": {"purpose": "You are a careful legal reviewer."},
    }
    with pytest.raises(RoleLintError) as excinfo:
        lint_role_payload(payload)
    violations = excinfo.value.violations
    assert any(v.rule_class == "R1" for v in violations)
    assert any(v.field_path == "attributes.purpose" for v in violations)


def test_r2_voice_embodiment_in_description_rejects() -> None:
    """Positive case T3 — 'embodies the perspective of' triggers R2."""
    payload = {
        "id": "role:adversary",
        "name": "Adversarial Review",
        "description": "This role embodies the perspective of a hostile reviewer.",
    }
    with pytest.raises(RoleLintError) as excinfo:
        lint_role_payload(payload)
    violations = excinfo.value.violations
    assert any(v.rule_class == "R2" for v in violations)
    assert any(v.field_path == "description" for v in violations)


def test_r3_first_person_identity_rejects() -> None:
    """Positive case T4 — 'I am the conscience of' triggers R3."""
    payload = {
        "id": "role:music-maker",
        "name": "Music Maker",
        "attributes": {"purpose": "I am the conscience of the team."},
    }
    with pytest.raises(RoleLintError) as excinfo:
        lint_role_payload(payload)
    violations = excinfo.value.violations
    assert any(v.rule_class == "R3" for v in violations)


def test_r3_catches_bard_em_dash_pattern() -> None:
    """Positive case T1 — bard's "The aperture —" pattern triggers R3.

    This is the calibration case the original spec flagged as a known
    false-negative; the bare-archetype pattern (R3 second variant) catches it.
    """
    payload = {
        "id": "role:bard",
        "name": "Bard",
        "description": (
            "Google Gemini-native agent. The aperture — associative fluency, "
            "live-web grounding, cross-domain synthesis. One of the music makers."
        ),
    }
    with pytest.raises(RoleLintError) as excinfo:
        lint_role_payload(payload)
    violations = excinfo.value.violations
    assert any(v.rule_class == "R3" for v in violations), (
        f"R3 must catch the em-dash form. Got: {[(v.rule_class, v.matched_fragment) for v in violations]}"
    )


def test_r4_our_team_emits_warning_not_error() -> None:
    """Negative case T6 — 'our team' is advisory R4, accepts with warning."""
    payload = {
        "id": "role:foo",
        "name": "Foo",
        "description": "This role coordinates with our team for batch processing.",
    }
    warnings = lint_role_payload(payload)
    assert len(warnings) >= 1
    assert all(w.severity == "warning" for w in warnings)
    assert any(w.rule_class == "R4" for w in warnings)


def test_nested_failure_mode_strings_linted() -> None:
    """Recursive walk catches identity-coded prose buried in attribute subtrees."""
    payload = {
        "id": "role:foo",
        "name": "Foo",
        "attributes": {
            "failure_mode": {
                "on_uncertainty": "you are encouraged to surface the issue",
            },
        },
    }
    with pytest.raises(RoleLintError) as excinfo:
        lint_role_payload(payload)
    violations = excinfo.value.violations
    assert any(v.rule_class == "R1" for v in violations)
    assert any("failure_mode" in v.field_path for v in violations)


def test_persona_seed_ref_uri_not_linted() -> None:
    """The URI itself is exempt — the file it points to is the persona surface."""
    payload = {
        "id": "role:foo",
        "name": "Foo",
        "description": "Operational role for foo.",
        "attributes": {
            "persona_seed_ref": "agent-identity/foo-birth.md",
        },
    }
    warnings = lint_role_payload(payload)
    assert warnings == []


def test_multiple_violations_all_surfaced() -> None:
    """A payload with multiple hits surfaces all of them in the exception."""
    payload = {
        "id": "role:bad",
        "name": "Bad",
        "description": "You are a reviewer who embodies the voice of a critic.",
        "attributes": {"purpose": "I am the conscience of the team."},
    }
    with pytest.raises(RoleLintError) as excinfo:
        lint_role_payload(payload)
    rule_classes = {v.rule_class for v in excinfo.value.violations}
    assert {"R1", "R2", "R3"}.issubset(rule_classes)
