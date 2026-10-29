"""Admit / reject JSON envelopes for team-dispatch-admit-v1 (no frontier_consult imports)."""

from __future__ import annotations

from typing import Any, Literal

from model_id import ModelId

Backend = Literal["cdp", "cursor_sdk", "api_role", "frontier", "cursor_ide"]


def is_cdp_model(model: str | None) -> bool:
    if not model or not str(model).strip():
        return False
    try:
        return ModelId.parse(model).backend_type == "cdp"
    except (TypeError, ValueError):
        return False


def is_cursor_substrate_model(model: str | None) -> bool:
    if not model or not str(model).strip():
        return False
    try:
        return ModelId.parse(model).backend_type == "cursor_sdk"
    except (TypeError, ValueError):
        return False


def reject_envelope(
    *,
    status_code: int,
    code: str,
    field: str,
    reason: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "admitted": False,
        "status_code": status_code,
        "code": code,
        "field": field,
        "reason": reason,
    }
    if details:
        out["details"] = details
    return out


def admit_envelope(
    *,
    backend: Backend,
    role: str | None,
    seat: str | None,
    model: str | None,
    job: str | None,
    workflow: str | None,
    skill_rows: list[Any] | None = None,
    to_agent: str | None = None,
    family: str | None = None,
    platform: str | None = None,
) -> dict[str, Any]:
    return {
        "admitted": True,
        "backend": backend,
        "role": role,
        "seat": seat,
        "model": model,
        "job": job,
        "workflow": workflow,
        "skill_rows": list(skill_rows or []),
        "to_agent": to_agent,
        "family": family,
        "platform": platform,
    }


def pass_through_reject(prior: dict[str, Any]) -> dict[str, Any]:
    if prior.get("admitted") is False:
        return dict(prior)
    return prior
