"""Model ban admit gate (route_policy model_bans)."""

from __future__ import annotations

import pytest

from implement_admission.impresario_policy import (
    ADVISOR_MODEL,
    refuse_argon_non_advisor_route,
)
from implement_admission.model_ban import (
    clear_model_ban_cache,
    evaluate_dispatch_model_ban,
    find_matching_ban,
    load_model_bans,
    normalize_model_for_ban,
)
from pipelines.team_dispatch_admit.v1.handlers.test_handlers import run_full_admit

pytestmark = pytest.mark.offline


def test_normalize_matches_xai_and_cursor_grok_slug() -> None:
    assert normalize_model_for_ban("cursor/grok-4.7") == normalize_model_for_ban(
        "xai/grok-4.7"
    )


def test_load_model_bans_includes_grok_and_argon() -> None:
    clear_model_ban_cache()
    bans = load_model_bans()
    slugs = {b.match_slug for b in bans}
    assert normalize_model_for_ban("cursor/grok-4.7") in slugs
    assert normalize_model_for_ban(ADVISOR_MODEL) in slugs


def test_explicit_grok_refused_with_403() -> None:
    plan = run_full_admit(
        {
            "op": "generate",
            "seat": "cursor-sdk",
            "model": "cursor/grok-4.7",
            "job": "freeform",
            "request_id": "ban-explicit",
        }
    )
    assert plan.get("admitted") is False
    assert plan.get("status_code") == 403
    assert plan.get("code") == "MODEL_BANNED"
    details = plan.get("details") or {}
    assert details.get("ref") == "agent-bus:9956"
    assert "ban_override" in details


def test_xai_grok_alias_refused() -> None:
    plan = run_full_admit(
        {
            "op": "generate",
            "seat": "cursor-sdk",
            "model": "xai/grok-4.7",
            "job": "freeform",
            "request_id": "ban-xai",
        }
    )
    assert plan.get("admitted") is False
    assert plan.get("code") == "MODEL_BANNED"


def test_omit_auto_judgment_refused_default() -> None:
    plan = run_full_admit(
        {
            "op": "generate",
            "seat": "cursor-sdk",
            "model": None,
            "job": "ask",
            "request_id": "ban-omit",
        }
    )
    assert plan.get("admitted") is False
    assert plan.get("code") == "MODEL_BANNED_DEFAULT"
    assert (plan.get("details") or {}).get(
        "omit_config_key"
    ) == "workflows.auto_judgment"


def test_ban_override_admits_grok() -> None:
    plan = run_full_admit(
        {
            "op": "generate",
            "seat": "cursor-sdk",
            "model": "cursor/grok-4.7",
            "job": "freeform",
            "ban_override": True,
            "ban_override_reason": "test waiver agent-bus:22498",
            "caller_agent": "cursor-sdk",
            "request_id": "ban-override",
        }
    )
    assert plan.get("admitted") is True, plan
    assert plan.get("model_ban_override", {}).get("model") == "cursor/grok-4.7"


def test_argon_role_bar_from_config() -> None:
    result = refuse_argon_non_advisor_route(
        model=ADVISOR_MODEL, impresario_role="critic"
    )
    assert result is not None
    assert result.code == "ARGON_ROLE_BARRED"


def test_override_pair_incomplete() -> None:
    reject, _ = evaluate_dispatch_model_ban(
        resolved_model="cursor/grok-4.7",
        explicit_pin=True,
        workflow_key=None,
        role=None,
        ban_override=True,
        ban_override_reason="",
        caller_agent=None,
    )
    assert reject is not None
    assert reject.get("code") == "ban_override_reason_required"


@pytest.mark.parametrize("role", ["critic", "implement", "sweep"])
def test_argon_ban_scoped_roles_only(role: str) -> None:
    entry = find_matching_ban(ADVISOR_MODEL, role=role)
    assert entry is not None
    entry_adv = find_matching_ban(ADVISOR_MODEL, role="advisor")
    assert entry_adv is None
