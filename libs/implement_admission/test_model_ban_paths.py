"""Entry-point tests for model_bans (not direct check_model_ban-only)."""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest
import yaml

from implement_admission.model_ban import clear_model_ban_cache
from services.git_integration_worker.cursor_sdk_model_ban import (
    evaluate_cursor_dispatch_model_ban,
    models_for_ban_check,
)
from services.git_integration_worker.models.cursor_api import CursorDispatchRequest

_REPO = Path(__file__).resolve().parents[2]


def test_structural_guard_model_bearing_admit_paths() -> None:
    """Break: a model-bearing admit entry stops calling the shared ban gate."""
    hop = (_REPO / "services/mcp-server/tools/agent_bus/hop.py").read_text()
    assert "check_model_ban_safe" in hop
    handoff = (
        _REPO / "services/universal-stargate/systems/frontier_consult/route_handoff.py"
    ).read_text()
    assert "check_model_ban_safe" in handoff
    giw_route = (
        _REPO / "services/git_integration_worker/routes/cursor_sdk.py"
    ).read_text()
    assert "evaluate_cursor_dispatch_model_ban" in giw_route
    assert (
        "check_model_ban(" not in giw_route.split("admit_cursor_dispatch", 1)[-1][:800]
    )


def test_evaluate_cursor_dispatch_model_ban_refuses_grok() -> None:
    clear_model_ban_cache()
    req = CursorDispatchRequest(
        thread_id="22499",
        model="cursor/grok-4.7",
        dispatch_id="d-ban-1",
        execution_id="e-ban-1",
        message="ban probe",
    )
    verdict, _ = evaluate_cursor_dispatch_model_ban(req)
    assert verdict.allowed is False
    assert (verdict.reject_envelope or {}).get("code") == "MODEL_BANNED"


def test_evaluate_cursor_dispatch_model_ban_accepts_waiver() -> None:
    clear_model_ban_cache()
    req = CursorDispatchRequest(
        thread_id="22499",
        model="cursor/grok-4.7",
        dispatch_id="d-ban-2",
        execution_id="e-ban-2",
        message="ban probe",
        ban_override=True,
        ban_override_reason="audit agent-bus:22498",
        model_ban_override_event_id="evt-22498",
    )
    verdict, _ = evaluate_cursor_dispatch_model_ban(req)
    assert verdict.allowed is True


def test_hop_refusal_does_not_advertise_ban_override() -> None:
    from implement_admission.model_ban import check_model_ban_safe

    verdict = check_model_ban_safe(
        resolved_model="cursor/grok-4.7",
        explicit_pin=True,
        workflow_key="agent_bus.hop",
        advertise_ban_override=False,
    )
    assert verdict.allowed is False
    details = (verdict.reject_envelope or {}).get("details") or {}
    assert "ban_override" not in details
    assert "ban_override_reason" not in details


def test_handoff_refusal_does_not_advertise_ban_override() -> None:
    from implement_admission.model_ban import check_model_ban_safe

    verdict = check_model_ban_safe(
        resolved_model="cursor/grok-4.7",
        explicit_pin=True,
        workflow_key="team.handoff",
        advertise_ban_override=False,
    )
    assert verdict.allowed is False
    details = (verdict.reject_envelope or {}).get("details") or {}
    assert "ban_override" not in details


@pytest.mark.asyncio
async def test_hop_dispatch_entry_rejects_banned_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break: _hop_dispatch drops the ban gate."""
    import sys

    mcp_root = _REPO / "services/mcp-server"
    if str(mcp_root) not in sys.path:
        sys.path.insert(0, str(mcp_root))
    from tools.agent_bus.hop import _hop_dispatch

    monkeypatch.setattr(
        "tools.agent_bus.hop.resolve_hop_successor_model",
        lambda _m: "cursor/grok-4.7",
    )
    result = await _hop_dispatch(
        thread="22499",
        from_agent="web-anthropic",
        desired_model="cursor/grok-4.7",
        reason="model-ban-path-test",
    )
    assert result.get("reason") in (
        "MODEL_BANNED",
        "model_banned",
        "MODEL_BANNED_DEFAULT",
    ) or ("banned" in str(result.get("error", "")).lower())


def test_agents_yaml_default_and_allowed_exclude_scope_all_bans() -> None:
    from implement_admission.model_ban import load_model_bans, normalize_model_for_ban
    from implement_admission.routing import load_route_policy

    clear_model_ban_cache()
    banned = {b.match_slug for b in load_model_bans() if b.scope_all}
    agents_path = _REPO / "config/agents.yaml"
    agents = yaml.safe_load(agents_path.read_text(encoding="utf-8"))
    offenders: list[str] = []
    for slug, block in (agents.get("agents") or {}).items():
        if not isinstance(block, dict):
            continue
        dm = str(block.get("default_model") or "").strip()
        if dm:
            try:
                if normalize_model_for_ban(dm) in banned:
                    offenders.append(f"agents.{slug}.default_model={dm!r}")
            except (TypeError, ValueError):
                pass
        for model in block.get("allowed_models") or []:
            ms = str(model).strip()
            if not ms:
                continue
            try:
                if normalize_model_for_ban(ms) in banned:
                    offenders.append(f"agents.{slug}.allowed_models {ms!r}")
            except (TypeError, ValueError):
                pass
    policy = load_route_policy()
    imp = policy.get("impresario") or {}
    adv = imp.get("advisor") or {}
    mech = imp.get("mechanical") or {}
    for key, model in (
        ("advisor.model", adv.get("model")),
        ("mechanical.model", mech.get("model")),
    ):
        ms = str(model or "").strip()
        if ms:
            try:
                if normalize_model_for_ban(ms) in banned:
                    offenders.append(f"impresario {key}={ms!r}")
            except (TypeError, ValueError):
                pass
    assert offenders == [], offenders


def test_resume_checks_child_model_when_parent_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_model_ban._parent_resolved_model",
        lambda _pid: None,
    )
    req = CursorDispatchRequest(
        thread_id="22499",
        model="cursor/grok-4.7",
        dispatch_id="d-ban-resume-miss",
        execution_id="e-ban-resume-miss",
        message="x",
        resume_of="parent-missing",
    )
    assert models_for_ban_check(req) == ("cursor/grok-4.7",)
    verdict, _ = evaluate_cursor_dispatch_model_ban(req)
    assert verdict.allowed is False


def test_resume_refuses_when_child_grok_parent_composer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_model_ban._parent_resolved_model",
        lambda _pid: "cursor/composer-2.5",
    )
    req = CursorDispatchRequest(
        thread_id="22499",
        model="cursor/grok-4.7",
        dispatch_id="d-ban-resume-dual",
        execution_id="e-ban-resume-dual",
        message="x",
        resume_of="parent-composer",
    )
    assert models_for_ban_check(req) == (
        "cursor/grok-4.7",
        "cursor/composer-2.5",
    )
    verdict, model = evaluate_cursor_dispatch_model_ban(req)
    assert verdict.allowed is False
    assert model == "cursor/grok-4.7"


def test_admit_cursor_dispatch_signature_uses_helper_not_inline_ban() -> None:
    from services.git_integration_worker.routes import cursor_sdk as route_mod

    src = inspect.getsource(route_mod.admit_cursor_dispatch)
    assert "evaluate_cursor_dispatch_model_ban" in src
