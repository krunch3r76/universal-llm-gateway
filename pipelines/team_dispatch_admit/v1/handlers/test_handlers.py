"""Handler unit tests + thin compat helpers for retargeted frontier_consult tests."""

from __future__ import annotations

import pytest
from agent_seat.registry import normalize_agent_slug
from implement_admission.workflow_registry import (
    CHECK_REVIEW_WORKFLOW,
    load_workflow_registry,
)
from systems.frontier_consult.admission import FrontierEndpointError

from .assemble import evaluate_assemble
from .defaults import evaluate_defaults
from .defaults_split import _resolve_auto_seat_profile
from .defaults_split import _sdk_admission as sdk_admission
from .validate import evaluate_validate


def run_full_admit(opts: dict) -> dict:
    v = evaluate_validate(opts)
    d = evaluate_defaults(opts, v)
    return evaluate_assemble(v, d)


async def run_team_dispatch_admit_for_route_tests(
    *,
    body: object,
    request_id: str,
    execution_id: str = "",
    **_: object,
) -> dict[str, object]:
    """In-process admit plan for frontier_consult route tests (conftest autouse)."""
    opts = {
        "op": getattr(body, "op", None),
        "role": getattr(body, "role", None),
        "seat": getattr(body, "seat", None),
        "model": getattr(body, "model", None),
        "job": getattr(body, "job", None),
        "dispatch_id": getattr(body, "dispatch_id", None),
        "request_id": request_id,
    }
    if getattr(body, "op", None) == "spawn":
        opts["backend"] = getattr(body, "backend", None)
        opts["thread"] = getattr(body, "thread", None)
        opts["gui_host"] = getattr(body, "gui_host", None)
        opts["spawn_purpose"] = getattr(body, "spawn_purpose", None)
    plan = run_full_admit(opts)
    if plan.get("admitted") is False:
        raise FrontierEndpointError(
            request_id=request_id,
            field=str(plan.get("field") or "admit"),
            reason=str(plan.get("reason") or "rejected"),
            status_code=int(plan.get("status_code") or 422),
            code=str(plan.get("code") or "admit_rejected"),
            details=plan.get("details")
            if isinstance(plan.get("details"), dict)
            else None,
        )
    return plan


def _raise_from_plan(plan: dict, *, request_id: str) -> None:
    if plan.get("admitted") is not False:
        return
    raise FrontierEndpointError(
        request_id=request_id,
        field=str(plan.get("field") or "admit"),
        reason=str(plan.get("reason") or "rejected"),
        status_code=int(plan.get("status_code") or 422),
        code=str(plan.get("code") or "admit_rejected"),
        details=plan.get("details") if isinstance(plan.get("details"), dict) else None,
    )


def resolve_auto_seat_generate_target(
    seat: str,
    *,
    model: str | None,
    request_id: str,
    job: str | None = None,
    packet_kind: str | None = None,
) -> tuple[str, str, str, str]:
    del packet_kind
    plan = run_full_admit(
        {
            "op": "generate",
            "seat": seat,
            "model": model,
            "job": job,
            "request_id": request_id,
        }
    )
    _raise_from_plan(plan, request_id=request_id)
    return (
        str(plan.get("to_agent") or ""),
        str(plan.get("family") or ""),
        str(plan.get("platform") or ""),
        str(plan.get("model") or ""),
    )


def resolve_cursor_sdk_generate_target(
    role: str,
    *,
    model: str | None,
    request_id: str,
    job: str | None = None,
    packet_kind: str | None = None,
) -> tuple[str, str, str, str]:
    return resolve_auto_seat_generate_target(
        role,
        model=model,
        request_id=request_id,
        job=job,
        packet_kind=packet_kind,
    )


def enforce_generate_role_seat_exclusive(
    role: str | None,
    seat: str | None,
    *,
    request_id: str,
    model: str | None = None,
) -> None:
    plan = run_full_admit(
        {
            "op": "generate",
            "role": role,
            "seat": seat,
            "model": model,
            "request_id": request_id,
        }
    )
    _raise_from_plan(plan, request_id=request_id)


def is_auto_seat_generate_admission(
    *,
    seat: str | None = None,
    role: str | None = None,
    model: str | None = None,
    request_id: str,
) -> bool:
    del request_id
    if seat is not None and str(seat).strip():
        try:
            _resolve_auto_seat_profile(str(seat))
        except ValueError:
            return False
        return True
    if role is not None and normalize_agent_slug(role) == "cursor-sdk":
        return False
    return False


def is_cursor_sdk_generate_admission(
    role: str | None = None,
    *,
    seat: str | None = None,
    model: str | None = None,
    request_id: str,
) -> bool:
    del request_id
    return sdk_admission(role=role, seat=seat, model=model)


# contract | expected_model | workflow_slot (route_policy.yaml workflows block)
_GOLDEN_OMIT_TABLE: tuple[tuple[str, str, str], ...] = (
    ("answer", "cursor/grok-4.7", "auto_judgment"),
    ("ask", "cursor/grok-4.7", "auto_judgment"),
    ("confer", "cursor/grok-4.7", "auto_judgment"),
    ("verify", "cursor/grok-4.7", "auto_judgment"),
    ("execute", "cursor/grok-4.7", "auto_judgment"),
    ("propagate", "cursor/grok-4.7", "auto_judgment"),
    ("sketch", "cursor/grok-4.7", "auto_judgment"),
    ("none", "cursor/grok-4.7", "auto_judgment"),
    ("conductor", "cursor/grok-4.7", "auto_judgment"),
    ("implement", "cursor/composer-2.5", "mechanical_implement"),
    ("mechanical", "cursor/composer-2.5", "mechanical_implement"),
    ("investigate", "cursor/grok-4.7", "investigate"),
    ("recon", "cursor/grok-4.7", "investigate"),
    ("seed", "cursor/grok-4.7", "investigate"),
)


@pytest.mark.offline
def test_defaults_golden_omit_by_job() -> None:
    for contract, expected_model, workflow_slot in _GOLDEN_OMIT_TABLE:
        plan = run_full_admit(
            {
                "op": "generate",
                "seat": "cursor-sdk",
                "model": None,
                "job": contract,
                "request_id": f"golden-{contract}",
            }
        )
        assert plan.get("admitted") is True, plan
        assert plan.get("model") == expected_model, contract
        assert plan.get("workflow") == workflow_slot, contract


@pytest.mark.offline
def test_substrate_role_conflict_gatherer_cursor_model() -> None:
    plan = run_full_admit(
        {
            "op": "generate",
            "role": "gatherer",
            "model": "cursor/claude-opus-5",
            "request_id": "t-conflict",
        }
    )
    assert plan.get("admitted") is False
    assert plan.get("code") == "substrate_model_role_conflict"
    assert plan.get("field") == "role"


@pytest.mark.offline
def test_seat_unknown_claude_web() -> None:
    plan = run_full_admit(
        {
            "op": "generate",
            "seat": "claude-web",
            "request_id": "t-seat",
        }
    )
    assert plan.get("admitted") is False
    assert plan.get("code") == "seat_unknown"


@pytest.mark.offline
def test_model_only_cdp_admits() -> None:
    plan = run_full_admit(
        {
            "op": "generate",
            "model": "cdp/opus-5",
            "request_id": "t-cdp",
        }
    )
    assert plan.get("admitted") is True
    assert plan.get("backend") == "cdp"


@pytest.mark.offline
def test_validate_op_spawn_runs_not_skipped() -> None:
    skipped = evaluate_validate({"op": "to_thread", "request_id": "t-skip"})
    assert skipped.get("skipped") is True
    ok = evaluate_validate(
        {
            "op": "spawn",
            "backend": "cursor_ide",
            "thread": "house-1",
            "spawn_purpose": "one line",
            "request_id": "t-spawn",
        }
    )
    assert ok.get("skipped") is not True
    assert ok.get("admitted") is True


@pytest.mark.offline
def test_op_spawn_plan_backend_cursor_ide() -> None:
    plan = run_full_admit(
        {
            "op": "spawn",
            "backend": "cursor_ide",
            "thread": "house-1",
            "gui_host": ":8",
            "spawn_purpose": "ship the slice",
            "request_id": "t-spawn-plan",
        }
    )
    assert plan.get("admitted") is True, plan
    assert plan.get("backend") == "cursor_ide"


@pytest.mark.offline
def test_to_thread_frontier_default_role() -> None:
    plan = run_full_admit(
        {"op": "to_thread", "request_id": "t-tt"},
    )
    assert plan.get("admitted") is True
    assert plan.get("backend") == "frontier"
    assert plan.get("role") == "synthesizer"


@pytest.mark.offline
@pytest.mark.parametrize("role", ["reviewer", "skeptic"])
def test_check_review_role_omit_model_coerces_cursor_sdk(role: str) -> None:
    reg = load_workflow_registry()
    check_review = reg.workflows[CHECK_REVIEW_WORKFLOW]
    plan = run_full_admit(
        {"op": "generate", "role": role, "request_id": f"t-{role}"},
    )
    assert plan.get("admitted") is True, plan
    assert plan.get("seat") == "cursor-sdk"
    assert plan.get("model") == check_review.model
    assert plan.get("workflow") == CHECK_REVIEW_WORKFLOW


@pytest.mark.offline
def test_judgment_role_mechanical_cursor_profile_mismatch() -> None:
    plan = run_full_admit(
        {
            "op": "generate",
            "role": "reviewer",
            "model": "cursor/gemini-3.5-flash",
            "request_id": "t-mismatch",
        }
    )
    assert plan.get("admitted") is False
    assert plan.get("code") == "profile_mismatch"


@pytest.mark.offline
def test_assemble_prefixes_ledger_row() -> None:
    """Break: conductor assemble leaves packet_markdown without ledger_row."""
    row_id = "cd" * 16
    packet = "hello packet"
    out = evaluate_assemble(
        {"admitted": True},
        {
            "admitted": True,
            "job": "conductor",
            "row_id": row_id,
            "ledger_purpose": "ship it",
            "packet_markdown": packet,
            "packet_sha256": "old",
        },
    )
    assert out["packet_markdown"].startswith(f"ledger_row: {row_id}\npurpose: ship it\n")
    assert out["packet_markdown"].endswith(packet)


def test_assemble_recomputes_packet_sha256() -> None:
    """Break: packet_sha256 still digests the unprefixed markdown."""
    from implement_admission.admission_read import compute_packet_sha256

    row_id = "ef" * 16
    out = evaluate_assemble(
        {"admitted": True},
        {
            "admitted": True,
            "job": "conductor",
            "row_id": row_id,
            "packet_markdown": "body",
            "packet_sha256": "stale",
        },
    )
    assert out["packet_sha256"] == compute_packet_sha256(out["packet_markdown"])
    assert out["packet_sha256"] != "stale"


def test_assemble_without_row_id_unchanged() -> None:
    """Break: a plan with no row_id is rewritten or its sha changes."""
    out = evaluate_assemble(
        {"admitted": True},
        {
            "admitted": True,
            "job": "conductor",
            "packet_markdown": "body",
            "packet_sha256": "kept",
        },
    )
    assert out["packet_markdown"] == "body"
    assert out["packet_sha256"] == "kept"


@pytest.mark.asyncio
async def test_enrich_does_not_set_row_id_on_plan() -> None:
    """Break: enrich writes row_id onto the conductor plan."""
    from types import SimpleNamespace
    from unittest.mock import patch

    from systems.frontier_consult.conductor_admit_enrich import enrich_conductor_admit_plan

    wrap = SimpleNamespace(
        packet_markdown="pkt",
        packet_sha256="sha",
        packet_rel_path="rel",
        birth_scoreboard_markdown=None,
        gated=False,
    )
    body = SimpleNamespace(
        job="conductor",
        source_ref="todo:house",
        packet_path=None,
        hop_seq=0,
        hop_from=None,
        generation_options={},
        dispatch_thread_id=None,
        caller_agent=None,
        prompt=None,
    )
    with patch(
        "systems.frontier_consult.conductor_admit_enrich.prepare_conductor_packet",
        return_value=wrap,
    ):
        out = await enrich_conductor_admit_plan(
            body=body, plan={"admitted": True}, request_id="r-enrich"
        )
    assert "row_id" not in out
    assert out["packet_markdown"] == "pkt"


def test_handler_modules_do_not_import_frontier_consult() -> None:
    import importlib
    from pathlib import Path

    root = Path(__file__).resolve().parent
    modules = (
        ("__init__.py", "pipelines.team_dispatch_admit.v1.handlers"),
        ("validate.py", "pipelines.team_dispatch_admit.v1.handlers.validate"),
        ("defaults.py", "pipelines.team_dispatch_admit.v1.handlers.defaults"),
        ("assemble.py", "pipelines.team_dispatch_admit.v1.handlers.assemble"),
        ("_envelope.py", "pipelines.team_dispatch_admit.v1.handlers._envelope"),
        (
            "defaults_split.py",
            "pipelines.team_dispatch_admit.v1.handlers.defaults_split",
        ),
    )
    for filename, mod_name in modules:
        text = (root / filename).read_text(encoding="utf-8")
        assert "systems.frontier_consult" not in text, filename
        importlib.import_module(mod_name)
