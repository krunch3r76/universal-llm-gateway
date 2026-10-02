"""Purpose-keyed CDP skill floors (B2)."""

from __future__ import annotations

from claude_bundles.cdp_model_endpoint_staging import ensure_cdp_judgment_skills
from claude_bundles.cdp_skill_profiles import (
    infer_cdp_purpose,
    profile_slugs_for_purpose,
    profile_slugs_for_session,
)


def test_session_operator_proxy_and_mission_chip_ask_does_not() -> None:
    """Legal session keys select the floor. Underscore is not a profile key.

    Breaks when session=operator-proxy|mission drops cdp-operator-proxy, or
    when session=ask grows that chip. Refusal of operator_proxy is intake.
    """
    for session in ("operator-proxy", "mission"):
        floor = profile_slugs_for_session(session)
        assert floor[0] == "cdp-operator-proxy"
        assert "reasoning-posture" in floor
    ask = profile_slugs_for_session("ask")
    assert ask[:2] == ("architecture-invariants", "ulg-architecture")
    assert "cdp-operator-proxy" not in ask
    assert "cdp-operator-proxy" not in profile_slugs_for_session("operator_proxy")


def test_omitted_session_floor_ignores_quoted_purpose_line() -> None:
    quoted = "purpose=operator-proxy\n"
    assert "purpose=operator-proxy" in quoted
    floor = profile_slugs_for_session(None)
    assert floor == ("reasoning-posture",)
    assert "cdp-operator-proxy" not in floor


def test_omitted_purpose_stays_judgment_only() -> None:
    assert profile_slugs_for_purpose(None) == ("reasoning-posture",)
    assert ensure_cdp_judgment_skills(None) == ["reasoning-posture"]
    assert ensure_cdp_judgment_skills(None, purpose=None) == ["reasoning-posture"]


def test_ask_floor_prepends_arch_pair() -> None:
    assert ensure_cdp_judgment_skills(None, purpose="ask") == [
        "architecture-invariants",
        "ulg-architecture",
        "reasoning-posture",
        "hypothesize-simulate",
    ]
    assert ensure_cdp_judgment_skills(["reasoning-posture"], purpose="ask") == [
        "architecture-invariants",
        "ulg-architecture",
        "hypothesize-simulate",
        "reasoning-posture",
    ]


def test_review_produce_mission_floors() -> None:
    assert ensure_cdp_judgment_skills(None, purpose="review") == [
        "reasoning-posture",
        "consult-posture",
        "hypothesize-simulate",
    ]
    assert ensure_cdp_judgment_skills(None, purpose="produce") == ["reasoning-posture"]
    assert ensure_cdp_judgment_skills(None, purpose="mission") == [
        "cdp-operator-proxy",
        "reasoning-posture",
        "hypothesize-simulate",
    ]
    assert ensure_cdp_judgment_skills(None, purpose="operator-proxy") == [
        "cdp-operator-proxy",
        "reasoning-posture",
        "hypothesize-simulate",
    ]


def test_infer_purpose_explicit_wins() -> None:
    assert infer_cdp_purpose("review", "cdp/sonnet-5") == "review"
    assert infer_cdp_purpose("ask", "cdp/sonnet-5") == "ask"


def test_infer_purpose_omitted_sonnet_produce_else_ask() -> None:
    assert infer_cdp_purpose(None, "cdp/sonnet-5") == "produce"
    assert infer_cdp_purpose("", "cdp/sonnet") == "produce"
    assert infer_cdp_purpose(None, "cdp/opus-5") == "ask"
    assert infer_cdp_purpose(None, "cdp/fable") == "ask"
