"""Stargate admit model-set gate (AC 4–5, 7, 10–11)."""

from __future__ import annotations

import pytest
import yaml
from implement_admission.impresario_policy import ADVISOR_MODEL
from implement_admission.routing import load_route_policy

from .defaults import evaluate_defaults
from .test_handlers import run_full_admit


def _model_set_details(plan: dict) -> dict:
    details = plan.get("details") or {}
    assert "fix_hint" in details
    hint = details["fix_hint"]
    assert "composer-2.5" in hint and "grok-4.7" in hint
    assert details.get("allowed_models") == [
        "cursor/composer-2.5",
        "cursor/grok-4.7",
    ]
    return details


@pytest.mark.offline
def test_stargate_admit_explicit_other_models_argon() -> None:
    plan = run_full_admit(
        {
            "op": "generate",
            "seat": "cursor-sdk",
            "model": "cursor/gemini-4-argon",
            "request_id": "ms-argon-p",
        }
    )
    assert plan.get("admitted") is True, plan
    assert plan.get("model") == "cursor/gemini-4-argon"


@pytest.mark.offline
def test_stargate_admit_refuses_bare_non_member_prefix_check() -> None:
    plan = run_full_admit(
        {
            "op": "generate",
            "seat": "cursor-sdk",
            "model": "gemini-4-argon",
            "request_id": "ms-argon-bare",
        }
    )
    assert plan.get("admitted") is False
    assert plan.get("code") == "sdk_generate_model_invalid"
    _model_set_details(plan)


@pytest.mark.offline
def test_stargate_admit_members() -> None:
    plan = run_full_admit(
        {
            "op": "generate",
            "seat": "cursor-sdk",
            "model": "cursor/composer-2.5",
            "request_id": "ms-member-composer",
        }
    )
    assert plan.get("admitted") is True, plan
    assert plan.get("model") == "cursor/composer-2.5"


@pytest.mark.offline
def test_stargate_admit_grok_member_banned() -> None:
    plan = run_full_admit(
        {
            "op": "generate",
            "seat": "cursor-sdk",
            "model": "cursor/grok-4.7",
            "request_id": "ms-member-grok",
        }
    )
    assert plan.get("admitted") is False
    assert plan.get("code") == "MODEL_BANNED"


@pytest.mark.offline
def test_stargate_admit_explicit_other_models_pool_pin() -> None:
    plan = run_full_admit(
        {
            "op": "generate",
            "seat": "cursor-sdk",
            "model": "cursor/claude-opus-5-5",
            "request_id": "ms-opus-explicit",
        }
    )
    assert plan.get("admitted") is True, plan
    assert plan.get("model") == "cursor/claude-opus-5-5"


@pytest.mark.offline
def test_conductor_admits_explicit_other_models_argon() -> None:
    plan = run_full_admit(
        {
            "op": "generate",
            "job": "conductor",
            "model": "cursor/gemini-4-argon",
            "request_id": "ms-conductor-argon",
            "source_ref": "todo:model-set-test",
        }
    )
    assert plan.get("admitted") is True, plan
    assert plan.get("model") == "cursor/gemini-4-argon"


@pytest.mark.offline
def test_conductor_default_still_admits_member() -> None:
    plan = run_full_admit(
        {
            "op": "generate",
            "job": "conductor",
            "request_id": "ms-conductor-default",
            "source_ref": "todo:model-set-test",
        }
    )
    assert plan.get("admitted") is True, plan
    assert plan.get("backend") == "cursor_sdk"
    assert plan.get("model") == "cursor/composer-2.5"


@pytest.mark.offline
def test_cdp_conductor_model_not_refused_by_cursor_set_gate() -> None:
    v = {"admitted": True}
    plan = evaluate_defaults(
        {
            "op": "generate",
            "job": "conductor",
            "model": "cdp/opus-5.5-high",
            "request_id": "ms-conductor-cdp",
            "source_ref": "todo:model-set-test",
        },
        v,
    )
    assert (
        plan.get("admitted") is not False
        or plan.get("code") != "sdk_generate_model_invalid"
    )


def test_workflow_cursor_models_are_admitted() -> None:
    from cursor_capabilities import is_cursor_sdk_model_admitted

    policy = load_route_policy()
    workflows = policy.get("workflows") or {}
    imp = policy.get("impresario") or {}
    for slug, binding in workflows.items():
        if not isinstance(binding, dict):
            continue
        model = binding.get("model")
        if model and str(model).startswith("cursor/"):
            assert is_cursor_sdk_model_admitted(str(model)), slug
    for role in ("driver", "mechanical", "ear", "ear_patrol"):
        block = imp.get(role)
        if isinstance(block, dict):
            model = block.get("model")
            if model and str(model).startswith("cursor/"):
                assert is_cursor_sdk_model_admitted(str(model)), role


def test_advisor_model_not_admitted_on_omit_path() -> None:
    from cursor_capabilities import is_cursor_sdk_model_admitted

    assert is_cursor_sdk_model_admitted(ADVISOR_MODEL) is False
    assert is_cursor_sdk_model_admitted(ADVISOR_MODEL, explicit_pin=True) is True


@pytest.mark.offline
def test_advisor_model_stargate_admit_explicit_pin() -> None:
    plan = run_full_admit(
        {
            "op": "generate",
            "seat": "cursor-sdk",
            "model": ADVISOR_MODEL,
            "request_id": "ms-advisor-pin",
        }
    )
    assert plan.get("admitted") is True, plan
    assert plan.get("model") == ADVISOR_MODEL


def test_comment_only_yaml_unchanged() -> None:
    import subprocess
    from pathlib import Path

    repo = Path(__file__).resolve().parents[4]

    def _master_yaml(rel: str) -> dict:
        raw = subprocess.run(
            ["git", "show", f"master:{rel}"],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        return yaml.safe_load(raw)

    for rel in ("config/agents.yaml",):
        assert yaml.safe_load((repo / rel).read_text(encoding="utf-8")) == _master_yaml(
            rel
        )
