"""Thread routes - CRUD for conversation threads (package-shadow split).

Single shared ``router`` instance; each sibling module attaches its routes
to it directly (imported before the submodules below so the name is bound
in this package's namespace when they run ``from . import router``).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ...auth import require_token

router = APIRouter(dependencies=[Depends(require_token)])

from . import (  # noqa: E402,F401
    associations,
    crud,
    detail,
    dispatch,
    lineage,
    send,
    send_prep,
    send_sidecar,
    triage,
    with_turn,
)

__all__ = ["router"]
