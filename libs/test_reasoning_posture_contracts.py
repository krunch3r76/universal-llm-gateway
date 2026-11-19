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
    assert resolve_delegation_tier("freeform", model="composer-2.5") == "C"
    assert resolve_delegation_tier("implement", model="composer-2.5") == "C"
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


def test_posture_mandatory_model_forms() -> None:
    from reasoning_posture_contracts import posture_mandatory_model

    assert posture_mandatory_model("cursor/composer-2.5")
    assert posture_mandatory_model("composer-2.5")
    assert posture_mandatory_model("CURSOR/Grok-4.7")
    assert posture_mandatory_model(" grok-4.7 ")
    assert not posture_mandatory_model(None)
    assert not posture_mandatory_model("")
    assert not posture_mandatory_model("cursor/auto")
    assert not posture_mandatory_model("cursor/claude-opus-5-5")
    assert not posture_mandatory_model("openai/gpt-5")


def test_warrants_injection_mandatory_model_overrides_skip_set() -> None:
    from reasoning_posture_contracts import (
        REASONING_POSTURE_SKIP_CONTRACTS,
        reasoning_posture_warrants_injection,
    )

    for c in REASONING_POSTURE_SKIP_CONTRACTS:
        assert reasoning_posture_warrants_injection(
            c, resolved_model="cursor/composer-2.5"
        )
        assert not reasoning_posture_warrants_injection(c, resolved_model="cursor/auto")


def test_warrants_injection_default_argument_is_contract_only() -> None:
    from job_vocab.records import BY_NAME
    from reasoning_posture_contracts import (
        REASONING_POSTURE_SKIP_CONTRACTS,
        reasoning_posture_warrants_injection,
    )

    for name in BY_NAME:
        assert reasoning_posture_warrants_injection(name) == (
            name not in REASONING_POSTURE_SKIP_CONTRACTS
        )


def test_posture_harness_report_reads_the_prompt() -> None:
    from reasoning_posture_contracts import (
        WHOLE_FILE_READ_BLOCK,
        posture_harness_report,
    )

    assert posture_harness_report(
        "/reasoning-posture\n\nbody", resolved_model=None
    ) == {
        "reasoning_posture": True,
        "whole_file_read": False,
        "mandatory_model": False,
        "source": "giw.admit_prompt",
    }
    assert posture_harness_report(
        "Use the `reasoning-posture` skill: x",
        resolved_model="cursor/composer-2.5",
    ) == {
        "reasoning_posture": True,
        "whole_file_read": False,
        "mandatory_model": True,
        "source": "giw.admit_prompt",
    }
    assert posture_harness_report("body only", resolved_model="cursor/auto") == {
        "reasoning_posture": False,
        "whole_file_read": False,
        "mandatory_model": False,
        "source": "giw.admit_prompt",
    }
    assert posture_harness_report(
        "/reasoning-posture\n\n" + WHOLE_FILE_READ_BLOCK,
        resolved_model="cursor/grok-4.7",
    ) == {
        "reasoning_posture": True,
        "whole_file_read": True,
        "mandatory_model": True,
        "source": "giw.admit_prompt",
    }


def test_posture_staging_skills() -> None:
    from reasoning_posture_contracts import posture_staging_skills

    assert posture_staging_skills(None, resolved_model="cursor/composer-2.5") == [
        "reasoning-posture"
    ]
    assert posture_staging_skills(["a", "b"], resolved_model="cursor/grok-4.7") == [
        "a",
        "b",
        "reasoning-posture",
    ]
    assert posture_staging_skills(
        ["agent_skill:reasoning-posture"], resolved_model="composer-2.5"
    ) == ["agent_skill:reasoning-posture"]
    assert posture_staging_skills(
        [" reasoning-posture "], resolved_model="composer-2.5"
    ) == [" reasoning-posture "]
    assert posture_staging_skills(None, resolved_model="cursor/auto") is None
    assert posture_staging_skills(["a"], resolved_model=None) == ["a"]
