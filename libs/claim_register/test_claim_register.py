"""Unit tests — Claimed construction fail-closed + render invariant."""

from __future__ import annotations

import pytest

from claim_register import (
    CLAIM_REGISTER_UNKNOWN,
    Claimed,
    claimed_derived,
    claimed_observed,
    normalize_claim_bearing_payload,
    render_claim,
)


def test_claimed_construction_rejects_unknown() -> None:
    with pytest.raises(ValueError, match="observed' or 'derived"):
        Claimed(register=CLAIM_REGISTER_UNKNOWN, value="x")  # type: ignore[arg-type]


def test_claimed_construction_rejects_arbitrary_register() -> None:
    with pytest.raises(ValueError, match="observed' or 'derived"):
        Claimed(register="inferred", value="x")  # type: ignore[arg-type]


def test_claimed_observed_and_derived_construct() -> None:
    obs = claimed_observed("gate_fired")
    der = claimed_derived("do the thing", basis="admit_gates")
    assert obs.register == "observed"
    assert der.register == "derived"
    assert der.to_wire() == {
        "register": "derived",
        "value": "do the thing",
        "basis": "admit_gates",
    }


def test_render_observed_may_be_bare() -> None:
    text = render_claim(claimed_observed("vision_field_missing"))
    assert text == "vision_field_missing"
    assert "derived" not in text.lower()


def test_render_derived_must_not_be_bare_observed_prose() -> None:
    value = "Add a vision: line and re-issue."
    text = render_claim(claimed_derived(value))
    assert text != value
    assert text.startswith("derived:")
    assert value in text


def test_render_derived_with_basis() -> None:
    text = render_claim(claimed_derived("counsel", basis="member4"))
    assert text == "(derived; basis=member4) counsel"


def test_render_unknown_is_loud() -> None:
    text = render_claim(
        {"register": CLAIM_REGISTER_UNKNOWN, "value": "bare hint", "basis": "post"}
    )
    assert "UNKNOWN_REGISTER" in text
    assert "bare hint" in text


def test_wire_normalize_stamps_bare_fix_hint_unknown_without_raising() -> None:
    payload = {"summary": "blocked", "fix_hint": "bare string hint"}
    out = normalize_claim_bearing_payload(payload)
    assert out["fix_hint"]["register"] == CLAIM_REGISTER_UNKNOWN
    assert out["fix_hint"]["value"] == "bare string hint"
    assert out["summary"] == "blocked"
    # Original left intact when rewrite needed (shallow copy).
    assert payload["fix_hint"] == "bare string hint"


def test_wire_normalize_passes_typed_fix_hint() -> None:
    typed = claimed_derived("typed hint").to_wire()
    payload = {"fix_hint": typed}
    out = normalize_claim_bearing_payload(payload)
    assert out["fix_hint"] is typed
    assert out["fix_hint"]["register"] == "derived"


def test_wire_normalize_passes_typed_claim_register() -> None:
    """Member 3 — claim_register key is in the post_terminal_status guard set."""
    typed = claimed_derived(
        "Episode superseded…", basis="supersede.dispositional_summary"
    ).to_wire()
    payload = {"claim_register": typed, "revert_disposition": "revert-pending"}
    out = normalize_claim_bearing_payload(payload)
    assert out["claim_register"] is typed
    assert out["claim_register"]["register"] == "derived"
    assert out["revert_disposition"] == "revert-pending"
