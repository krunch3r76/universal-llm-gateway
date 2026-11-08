"""Purpose-keyed CDP skill floors (B2)."""

from __future__ import annotations

from pathlib import Path

import pytest

from claude_bundles.cdp_model_endpoint_staging import (
    ensure_cdp_judgment_skills,
    stage_cdp_prompt_with_skills,
)
from claude_bundles.cdp_skill_profiles import (
    infer_cdp_purpose,
    profile_slugs_for_purpose,
    profile_slugs_for_session,
    service_dirs,
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


def test_omitted_purpose_review_contract_uses_review_floor() -> None:
    """K30 — contract=code-review without purpose gets review skill floor only."""
    assert profile_slugs_for_purpose(None, contract="code-review") == (
        "reasoning-posture",
        "consult-posture",
        "hypothesize-simulate",
    )
    assert ensure_cdp_judgment_skills(None, purpose=None, contract="code-review") == [
        "reasoning-posture",
        "consult-posture",
        "hypothesize-simulate",
    ]
    assert ensure_cdp_judgment_skills(None, purpose=None, contract="freeform") == [
        "reasoning-posture",
    ]


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


def test_service_dirs_two_roots_and_tests_under_service() -> None:
    """(d) tests/ under a service still counts the service dir only."""
    assert service_dirs(
        [
            "services/mcp-server/foo.py",
            "libs/bus_watch/bar.py",
            "services/gateway/tests/test_x.py",
        ]
    ) == frozenset(
        {"services/mcp-server", "libs/bus_watch", "services/gateway"}
    )


def test_review_contract_multi_service_files_prepends_arch_pair() -> None:
    """(a) Two service dirs → arch pair prepended on omitted-purpose review path."""
    files = ["services/foo/a.py", "libs/bar/b.py"]
    assert profile_slugs_for_purpose(
        None, contract="code-review", files_expected=files
    )[:2] == ("architecture-invariants", "ulg-architecture")
    assert ensure_cdp_judgment_skills(
        None, purpose=None, contract="code-review", files_expected=files
    )[:2] == ["architecture-invariants", "ulg-architecture"]


def test_review_contract_single_service_dir_unchanged_floor() -> None:
    """(b) One production root → review floor only."""
    one = ["services/foo/a.py", "services/foo/b.py"]
    assert profile_slugs_for_purpose(None, contract="code-review", files_expected=one) == (
        "reasoning-posture",
        "consult-posture",
        "hypothesize-simulate",
    )


def test_review_contract_explicit_purpose_ignores_files_expected() -> None:
    """(c) Explicit purpose wins — no arch pair from files_expected alone."""
    files = ["services/a/x.py", "libs/b/y.py"]
    assert profile_slugs_for_purpose("review", contract="code-review", files_expected=files) == (
        "reasoning-posture",
        "consult-posture",
        "hypothesize-simulate",
    )


def test_stage_multi_service_code_review_appends_architecture_check_line(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Staging emits one architecture-check line after the skills marker."""
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    body = (
        "## Review\n"
        "files_expected:\n"
        "- services/alpha/a.py\n"
        "- libs/beta/b.py\n"
        + ("detail " * 40)
        + "\n"
    )
    staged = stage_cdp_prompt_with_skills(
        execution_id="exec-cr-multi-arch",
        prompt_text=body,
        purpose=None,
        contract="code-review",
    )
    text = (
        tmp_path / "notes/system/ephemeral/cdp-endpoint/exec-cr-multi-arch/prompt.md"
    ).read_text(encoding="utf-8")
    assert staged.staged is True
    assert "Architecture check: md_list docs/architecture/" in text
    assert "libs/beta, services/alpha" in text
