"""Tests for review_defaults loader."""

from __future__ import annotations

import pytest

from implement_admission.review_defaults import ReviewDefault, load_review_default
from implement_admission.routing import load_route_policy


def test_load_review_default_spec_from_disk() -> None:
    spec = load_review_default("spec")
    assert spec == ReviewDefault(
        model="cursor/composer-2.5", contract=None, effort=None
    )


def test_load_review_default_implementation_contract_effort() -> None:
    row = load_review_default("implementation")
    assert row.model == "cdp/opus-5"
    assert row.contract == "code-review"
    assert row.effort == "high"


def test_load_review_default_implementation_claude_author() -> None:
    row = load_review_default("implementation_claude_author")
    assert row.model == "cursor/composer-2.5"
    assert row.contract == "check-review"
    assert row.effort is None


def test_load_review_default_unknown_kind() -> None:
    policy = load_route_policy()
    with pytest.raises(ValueError, match="missing entry for kind 'nope'"):
        load_review_default("nope", policy=policy)


def test_load_review_default_missing_review_defaults_mapping() -> None:
    with pytest.raises(ValueError, match="missing review_defaults mapping"):
        load_review_default("spec", policy={})


def test_load_review_default_empty_policy_dict_does_not_read_disk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "implement_admission.review_defaults.load_route_policy",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("disk load")),
    )
    with pytest.raises(ValueError, match="missing review_defaults mapping"):
        load_review_default("spec", policy={})


def test_load_review_default_unknown_row_key() -> None:
    policy = {
        "review_defaults": {
            "spec": {"model": "cursor/composer-2.5", "seat": "cursor-sdk"},
        }
    }
    with pytest.raises(ValueError, match="unknown key 'seat'"):
        load_review_default("spec", policy=policy)


def test_load_review_default_empty_model() -> None:
    policy = {"review_defaults": {"spec": {"model": ""}}}
    with pytest.raises(ValueError, match=r"review_defaults\.spec\.model"):
        load_review_default("spec", policy=policy)


def test_load_review_default_cloud_api_refused() -> None:
    policy = {
        "review_defaults": {
            "spec": {"model": "anthropic/claude-sonnet-5-5"},
        }
    }
    with pytest.raises(ValueError, match=r"review_defaults\.spec.*cloud_api"):
        load_review_default("spec", policy=policy)


def test_route_policy_yaml_spec_has_no_seat_contract_effort() -> None:
    policy = load_route_policy()
    spec = policy["review_defaults"]["spec"]
    assert spec == {"model": "cursor/composer-2.5"}
