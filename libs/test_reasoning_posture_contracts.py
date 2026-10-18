"""resolve_delegation_tier contract tests."""

from __future__ import annotations

from reasoning_posture_contracts import resolve_delegation_tier


def test_resolve_delegation_tier_cdp_purpose_orchestrator() -> None:
    assert resolve_delegation_tier("implement", purpose="operator-proxy") == "A"
    assert resolve_delegation_tier("implement", purpose="mission") == "A"


def test_resolve_delegation_tier_freeform_composer_or_nested() -> None:
    assert (
        resolve_delegation_tier(
            "freeform", model="cursor/composer-2.5", nest_under=None
        )
        == "C"
    )
    assert resolve_delegation_tier("freeform", model="cursor/grok-4.7", nest_under="p") == "C"


def test_resolve_delegation_tier_known_contracts() -> None:
    assert resolve_delegation_tier("conductor") == "A"
    assert resolve_delegation_tier("investigate") == "B"
    assert resolve_delegation_tier("implement") == "C"


def test_resolve_delegation_tier_unknown_defaults() -> None:
    assert resolve_delegation_tier("not-a-job", model="cursor/composer-2.5") == "C"
    assert resolve_delegation_tier("not-a-job", model="cursor/grok-4.7") == "B"


def test_lane_does_not_change_tier() -> None:
    assert resolve_delegation_tier("implement", lane="B") == "C"
    assert resolve_delegation_tier("implement", lane="A") == "C"
