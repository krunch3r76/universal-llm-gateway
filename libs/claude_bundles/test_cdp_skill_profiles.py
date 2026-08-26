"""Purpose-keyed CDP skill floors (B2)."""

from __future__ import annotations

from claude_bundles.cdp_model_endpoint_staging import ensure_cdp_judgment_skills
from claude_bundles.cdp_skill_profiles import infer_cdp_purpose, profile_slugs_for_purpose


def test_omitted_purpose_stays_judgment_only() -> None:
    assert profile_slugs_for_purpose(None) == ("ulg-for-llms", "reasoning-posture")
    assert ensure_cdp_judgment_skills(None) == ["ulg-for-llms", "reasoning-posture"]
    assert ensure_cdp_judgment_skills(None, purpose=None) == [
        "ulg-for-llms",
        "reasoning-posture",
    ]


def test_ask_floor_prepends_arch_pair() -> None:
    assert ensure_cdp_judgment_skills(None, purpose="ask") == [
        "architecture-invariants",
        "ulg-architecture",
        "ulg-for-llms",
        "reasoning-posture",
    ]
    assert ensure_cdp_judgment_skills(["reasoning-posture"], purpose="ask") == [
        "architecture-invariants",
        "ulg-architecture",
        "ulg-for-llms",
        "reasoning-posture",
    ]


def test_review_produce_mission_floors() -> None:
    assert ensure_cdp_judgment_skills(None, purpose="review") == [
        "ulg-for-llms",
        "reasoning-posture",
        "consult-posture",
    ]
    assert ensure_cdp_judgment_skills(None, purpose="produce") == [
        "ulg-for-llms",
        "reasoning-posture",
    ]
    assert ensure_cdp_judgment_skills(None, purpose="mission") == [
        "cdp-operator-proxy",
        "ulg-for-llms",
        "reasoning-posture",
    ]
    assert ensure_cdp_judgment_skills(None, purpose="operator-proxy") == [
        "cdp-operator-proxy",
        "ulg-for-llms",
        "reasoning-posture",
    ]


def test_infer_purpose_explicit_wins() -> None:
    assert infer_cdp_purpose("review", "cdp/sonnet-5") == "review"
    assert infer_cdp_purpose("ask", "cdp/sonnet-5") == "ask"


def test_infer_purpose_omitted_sonnet_produce_else_ask() -> None:
    assert infer_cdp_purpose(None, "cdp/sonnet-5") == "produce"
    assert infer_cdp_purpose("", "cdp/sonnet") == "produce"
    assert infer_cdp_purpose(None, "cdp/opus-5") == "ask"
    assert infer_cdp_purpose(None, "cdp/fable") == "ask"
