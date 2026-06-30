"""Stargate dispatch adapter for todo-sourced implement-readiness admission."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from implement_admission.implement_ready_gate import (
    ImplementReadyGateError,
)
from implement_admission.implement_ready_gate import (
    require_implement_ready as _require_implement_ready,
)

from .admission import FrontierEndpointError
from .implement_admission_bridge import StargateCortexReader
from .skeptic_evidence_grounding import evaluate_skeptic_evidence_grounding


def require_implement_ready(
    *,
    request_id: str,
    source_ref: str | None,
    cortex: StargateCortexReader,
) -> None:
    """Hard gate for todo-sourced implement dispatch. No-op for non-todo sources."""

    def _resolve_skeptic(*, assertion: dict[str, Any]) -> dict[str, Any]:
        outcome = evaluate_skeptic_evidence_grounding(
            reader=cortex,
            assertion=assertion,
            workspaces_root=None,
        )
        return {
            "evidence_grounded": outcome.grounded,
            "evidence_unresolved": outcome.unresolved,
            "evidence_mode": outcome.mode,
        }

    try:
        _require_implement_ready(
            request_id=request_id,
            source_ref=source_ref,
            cortex=cortex,
            workspaces_root=_workspaces_root(),
            resolve_skeptic=_resolve_skeptic,
        )
    except ImplementReadyGateError as exc:
        raise FrontierEndpointError(
            request_id=exc.request_id,
            field=exc.field,
            reason=exc.reason,
            status_code=exc.status_code,
            code=exc.code,
        ) from exc


def _workspaces_root() -> Path:
    from .handoff import _workspaces_root as root

    return root()


__all__ = ["require_implement_ready"]
