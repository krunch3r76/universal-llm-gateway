"""Handler unit tests + thin compat helpers for retargeted frontier_consult tests."""

from __future__ import annotations

import pytest
from agent_seat.registry import normalize_agent_slug
from implement_admission.workflow_registry import AUTO_OMIT_CONTRACTS

from systems.frontier_consult.admission import FrontierEndpointError

from .assemble import evaluate_assemble
from .defaults import _sdk_admission as sdk_admission
from .defaults import evaluate_defaults
from .validate import evaluate_validate


def run_full_admit(opts: dict) -> dict:
    v = evaluate_validate(opts)
    d = evaluate_defaults(opts, v)
    return evaluate_assemble(v, d)


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
        from .defaults import _resolve_auto_seat_profile

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


@pytest.mark.offline
def test_defaults_golden_omit_by_job() -> None:
    jobs = sorted(AUTO_OMIT_CONTRACTS | {"mechanical"})
    rows: dict[str, str] = {}
    for job in jobs:
        plan = run_full_admit(
            {
                "op": "generate",
                "seat": "cursor-sdk",
                "model": None,
                "job": job,
                "request_id": f"golden-{job}",
            }
        )
        assert plan.get("admitted") is True, plan
        rows[job] = str(plan.get("model"))
    assert rows["mechanical"] == "cursor/composer-2.5"
    assert rows["implement"] == "cursor/composer-2.5"
    for j in ("investigate", "recon", "seed"):
        assert rows[j] == "cursor/grok-4.7"
    assert rows["conductor"] == "cursor/grok-4.7"


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
def test_to_thread_frontier_default_role() -> None:
    plan = run_full_admit(
        {"op": "to_thread", "request_id": "t-tt"},
    )
    assert plan.get("admitted") is True
    assert plan.get("backend") == "frontier"
    assert plan.get("role") == "synthesizer"
