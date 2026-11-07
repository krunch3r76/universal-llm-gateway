"""Model ban admit gate (route_policy model_bans)."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
import yaml

from implement_admission.impresario_policy import (
    advisor_model_wire,
    refuse_argon_non_advisor_route,
)
from implement_admission.model_ban import (
    check_model_ban,
    clear_model_ban_cache,
    evaluate_dispatch_model_ban,
    find_matching_ban,
    load_model_bans,
    model_ban_wire_fields,
    normalize_model_for_ban,
)
from implement_admission.model_ban_load import (
    ModelBansUnloadableError,
    load_model_bans_strict,
    parse_model_bans,
)
from implement_admission.routing import load_route_policy
from implement_admission.team_dispatch_admit_config import (
    load_conductor_model_defaults,
)
from pipelines.team_dispatch_admit.v1.handlers.test_handlers import run_full_admit

pytestmark = pytest.mark.offline

GROK_SLUG = "grok-4.7"
ADVISOR = advisor_model_wire()


def test_normalize_matches_xai_and_cursor_grok_slug() -> None:
    assert normalize_model_for_ban("cursor/grok-4.7") == normalize_model_for_ban(
        "xai/grok-4.7"
    )


@pytest.mark.parametrize(
    ("wire", "banned"),
    [
        ("cursor/grok-4.7", True),
        ("xai/grok-4.7", True),
        ("grok-4.7", True),
        ("CURSOR/Grok-4.7", True),
        ("  cursor/grok-4.7  ", True),
        ("cursor/grok-4.7-high", True),
        ("cursor/grok-4.7-fast", True),
        ("cursor/grok-4.7-max", True),
        ("grok-4.7__effort_high", True),
        ("grok-4.70", False),
        ("grok-4.7x", False),
        ("cursor/composer-2.5", False),
        ("gro\u0430k-4.7", False),
    ],
)
def test_normalize_ban_table(wire: str, banned: bool) -> None:
    clear_model_ban_cache()
    slug = normalize_model_for_ban(wire)
    if banned:
        assert slug == GROK_SLUG
        assert find_matching_ban(wire) is not None
    elif wire in {"grok-4.70", "grok-4.7x"}:
        assert slug != GROK_SLUG
        assert find_matching_ban(wire) is None


def test_load_model_bans_includes_grok_and_argon() -> None:
    clear_model_ban_cache()
    bans = load_model_bans()
    slugs = {b.match_slug for b in bans}
    assert normalize_model_for_ban("cursor/grok-4.7") in slugs
    assert normalize_model_for_ban(ADVISOR) in slugs


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


def test_omit_ask_admits_composer_not_grok() -> None:
    plan = run_full_admit(
        {
            "op": "generate",
            "seat": "cursor-sdk",
            "model": None,
            "job": "ask",
            "request_id": "ban-omit-ask",
        }
    )
    assert plan.get("admitted") is True, plan
    assert plan.get("model") == "cursor/composer-2.5"
    assert plan.get("workflow") == "auto_judgment"


def test_no_workflows_model_resolves_to_scope_all_banned_model() -> None:
    """Config invariant: omit paths must not default to scope:all banned ids."""
    clear_model_ban_cache()
    policy = load_route_policy()
    banned_slugs = {b.match_slug for b in load_model_bans() if b.scope_all}
    workflows = policy.get("workflows") or {}
    offenders: list[str] = []
    for slug, binding in workflows.items():
        if not isinstance(binding, dict):
            continue
        model = str(binding.get("model") or "").strip()
        if not model:
            continue
        try:
            if normalize_model_for_ban(model) in banned_slugs:
                offenders.append(f"workflows.{slug}.model={model!r}")
        except (TypeError, ValueError):
            continue
    assert offenders == [], offenders


def test_pipeline_model_defaults_exclude_scope_all_bans() -> None:
    clear_model_ban_cache()
    banned = {b.match_slug for b in load_model_bans() if b.scope_all}
    defaults = load_conductor_model_defaults()
    offenders: list[str] = []
    for role, block in defaults.items():
        if not isinstance(block, dict):
            continue
        for key in ("model",):
            model = str(block.get(key) or "").strip()
            if not model:
                continue
            if normalize_model_for_ban(model) in banned:
                offenders.append(f"model_defaults.conductor.{role}={model!r}")
    allowed = defaults.get("allowed_models") or []
    if isinstance(allowed, list):
        for model in allowed:
            model_s = str(model).strip()
            if model_s and normalize_model_for_ban(model_s) in banned:
                offenders.append(f"allowed_models {model_s!r}")
    hop = defaults.get("hop_successor") or {}
    if isinstance(hop, dict):
        hm = str(hop.get("model") or "").strip()
        if hm and normalize_model_for_ban(hm) in banned:
            offenders.append(f"hop_successor={hm!r}")
    policy = load_route_policy()
    imp = policy.get("impresario") or {}
    critics = imp.get("allowed_critics") or []
    argon_slug = normalize_model_for_ban(ADVISOR)
    if isinstance(critics, list):
        for model in critics:
            ms = str(model).strip()
            if not ms:
                continue
            if normalize_model_for_ban(ms) in banned:
                offenders.append(f"impresario.allowed_critics {ms!r}")
            # role-scoped argon ban exempt within advisor role wire listing
            if normalize_model_for_ban(ms) == argon_slug:
                continue
    assert offenders == [], offenders


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
            "dispatch_id": "dispatch-override-1",
            "request_id": "ban-override",
        }
    )
    assert plan.get("admitted") is True, plan
    override = plan.get("model_ban_override") or {}
    assert override.get("model") == "cursor/grok-4.7"
    assert override.get("dispatch_id") == "dispatch-override-1"


def test_whitespace_only_override_reason_422() -> None:
    reject, _ = evaluate_dispatch_model_ban(
        resolved_model="cursor/grok-4.7",
        explicit_pin=True,
        workflow_key=None,
        role=None,
        ban_override=True,
        ban_override_reason="   ",
        caller_agent=None,
    )
    assert reject is not None
    assert reject.get("status_code") == 422
    assert reject.get("code") == "ban_override_reason_required"


def test_force_true_does_not_waive_ban() -> None:
    plan = run_full_admit(
        {
            "op": "generate",
            "seat": "cursor-sdk",
            "model": "cursor/grok-4.7",
            "job": "freeform",
            "force": True,
            "force_reason": "operator force",
            "request_id": "ban-force",
        }
    )
    assert plan.get("admitted") is False
    assert plan.get("code") == "MODEL_BANNED"


def test_generation_options_ban_override_ignored() -> None:
    plan = run_full_admit(
        {
            "op": "generate",
            "seat": "cursor-sdk",
            "model": "cursor/grok-4.7",
            "job": "freeform",
            "generation_options": {
                "ban_override": True,
                "ban_override_reason": "must not waive via nested options",
            },
            "request_id": "ban-gen-opt",
        }
    )
    assert plan.get("admitted") is False
    assert plan.get("code") == "MODEL_BANNED"
    enabled, reason, _ = model_ban_wire_fields(
        {
            "generation_options": {
                "ban_override": True,
                "ban_override_reason": "x",
            }
        }
    )
    assert enabled is False
    assert reason is None


def test_argon_role_bar_from_config() -> None:
    result = refuse_argon_non_advisor_route(model=ADVISOR, impresario_role="critic")
    assert result is not None
    assert result.code == "ARGON_ROLE_BARRED"
    assert "advisor" in (result.message or "").lower()


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
    entry = find_matching_ban(ADVISOR, role=role)
    assert entry is not None
    entry_adv = find_matching_ban(ADVISOR, role="advisor")
    assert entry_adv is None


def test_loader_missing_key_fail_closed() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "route_policy.yaml"
        path.write_text(yaml.safe_dump({"workflows": {}}), encoding="utf-8")
        with pytest.raises(ModelBansUnloadableError):
            load_model_bans_strict(path=path)


def test_loader_empty_list_ok() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "route_policy.yaml"
        path.write_text(yaml.safe_dump({"model_bans": []}), encoding="utf-8")
        assert load_model_bans_strict(path=path) == ()


def test_loader_malformed_row_raises() -> None:
    policy = {"model_bans": [{"model": ""}]}
    with pytest.raises(ValueError):
        parse_model_bans(policy)


def test_loader_cache_hits_strict_once(monkeypatch: pytest.MonkeyPatch) -> None:
    clear_model_ban_cache()
    calls: list[int] = []
    real = load_model_bans_strict

    def counting(**kwargs: object) -> tuple[object, ...]:
        calls.append(1)
        return real(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(
        "implement_admission.model_ban_load.load_model_bans_strict",
        counting,
    )
    load_model_bans()
    load_model_bans()
    assert len(calls) == 1
    clear_model_ban_cache()


def test_check_model_ban_public_api() -> None:
    verdict = check_model_ban(resolved_model="cursor/grok-4.7", explicit_pin=True)
    assert verdict.allowed is False
    assert verdict.reject_envelope is not None
    assert verdict.matched is not None


def test_to_thread_generate_ban_path() -> None:
    plan = run_full_admit(
        {
            "op": "to_thread",
            "model": "cursor/grok-4.7",
            "request_id": "ban-to-thread",
        }
    )
    assert plan.get("admitted") is False
    assert plan.get("code") == "MODEL_BANNED"


def test_conductor_hop_successor_default_passes_assemble_gate() -> None:
    plan = run_full_admit(
        {
            "op": "generate",
            "seat": "cursor-sdk",
            "job": "conductor",
            "hop_seq": 0,
            "request_id": "ban-conductor-hop",
        }
    )
    assert plan.get("admitted") is True, plan
    hop = plan.get("hop_successor_default")
    assert hop == "cursor/composer-2.5"


def test_hop_successor_model_banned_after_cdp_prefix() -> None:
    import sys
    from pathlib import Path

    mcp_root = Path(__file__).resolve().parents[2] / "services/mcp-server"
    if str(mcp_root) not in sys.path:
        sys.path.insert(0, str(mcp_root))
    from tools.agent_bus.hop import resolve_hop_successor_model

    resolved = resolve_hop_successor_model("cursor/grok-4.7")
    verdict = check_model_ban(
        resolved_model=resolved,
        explicit_pin=True,
        workflow_key="agent_bus.hop",
    )
    assert verdict.allowed is False
    bare = resolve_hop_successor_model("grok-4.7")
    assert bare == "cdp/grok-4.7"
    verdict_cdp = check_model_ban(resolved_model=bare, explicit_pin=True)
    assert verdict_cdp.allowed is False


def test_handoff_resolved_model_banned() -> None:
    verdict = check_model_ban(
        resolved_model="cursor/grok-4.7",
        explicit_pin=False,
        workflow_key="team.handoff",
    )
    assert verdict.allowed is False


def test_executor_override_model_banned() -> None:
    verdict = check_model_ban(
        resolved_model="cursor/grok-4.7",
        explicit_pin=True,
        workflow_key="team.handoff.executor_override",
    )
    assert verdict.allowed is False


def test_giw_resume_path_bans_explicit_grok() -> None:
    verdict = check_model_ban(
        resolved_model="cursor/grok-4.7",
        explicit_pin=True,
        workflow_key="git_integration_worker.resume_of",
        dispatch_id="child-resume",
    )
    assert verdict.allowed is False
    assert verdict.reject_envelope is not None


def test_unicode_lookalike_does_not_match_grok_ban() -> None:
    # Cyrillic 'а' in place of Latin 'a' — must not normalize to grok-4.7 ban slug.
    lookalike = "gro\u0430k-4.7"
    assert find_matching_ban(lookalike) is None


def test_validate_fail_closed_when_model_bans_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pipelines.team_dispatch_admit.v1.handlers.validate import evaluate_validate

    def _missing() -> tuple[object, ...]:
        raise ModelBansUnloadableError("model_bans key missing")

    monkeypatch.setattr("implement_admission.model_ban_load.load_model_bans", _missing)
    out = evaluate_validate({"op": "generate"})
    assert out.get("admitted") is False
    assert out.get("status_code") == 503
    assert out.get("code") == "MODEL_BANS_UNLOADABLE"


def test_override_event_payload_fields() -> None:
    _, override = evaluate_dispatch_model_ban(
        resolved_model="cursor/grok-4.7",
        explicit_pin=True,
        workflow_key=None,
        role=None,
        ban_override=True,
        ban_override_reason="audit agent-bus:22498",
        caller_agent="cursor-sdk",
        dispatch_id="d-22498",
    )
    assert override is not None
    assert override.get("model") == "cursor/grok-4.7"
    assert override.get("reason") == "audit agent-bus:22498"
    assert override.get("caller") == "cursor-sdk"
    assert override.get("dispatch_id") == "d-22498"
    assert override.get("ref") == "agent-bus:9956"


def test_model_knobs_fast_does_not_waive_banned_model() -> None:
    """fast knob does not change ban slug for cursor/grok-4.7."""
    plan = run_full_admit(
        {
            "op": "generate",
            "seat": "cursor-sdk",
            "model": "cursor/grok-4.7",
            "job": "freeform",
            "generation_options": {"model_knobs": {"fast": "true"}},
            "request_id": "ban-knobs-fast",
        }
    )
    assert plan.get("admitted") is False
    assert plan.get("code") == "MODEL_BANNED"


def test_config_ban_invariants_unified() -> None:
    """Single load-time sweep: workflows, pipeline defaults, impresario critics."""
    test_no_workflows_model_resolves_to_scope_all_banned_model()
    test_pipeline_model_defaults_exclude_scope_all_bans()
