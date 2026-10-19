"""Admission gates for team/persona and raw frontier dispatch."""

from __future__ import annotations

from job_vocab import HANDOFF_ADMITTED_JOBS, TO_THREAD_ADMITTED_JOBS

from .route_handoff import TeamHandoffBody, team_handoff
from .route_implement import (
    ImplementCloseoutBody,
    _closeout_dedupe,
    implement_closeout,
    implement_pinned_deliverable,
)
from .route_models import (
    TeamDispatchBody,
    TeamDispatchClaimBody,
    TeamDispatchClaimReleaseBody,
    TeamDispatchGenerateBody,
    TeamDispatchOccupancyBody,
    TeamDispatchSteerBody,
    TeamDispatchToThreadBody,
)
from .route_ops import (
    FrontierDispatchBody,
    FrontierDispatchGenerateBody,
    FrontierDispatchToThreadBody,
    _dispatch,
    _normalize_op_body,
    claim_id_from_generation_options,
    frontier_dispatch,
    hop_park_release_requested,
)
from .route_routers import frontier_router, implement_router, team_router
from .route_team_dispatch import team_dispatch

_TO_THREAD_ADMITTED_LITERAL = frozenset(
    {"bare", "freeform", "mechanical", "sketch", "implement"}
)
_HANDOFF_ADMITTED_LITERAL = frozenset(
    {"bare", "freeform", "mechanical", "sketch", "implement", "confer"}
)
assert TO_THREAD_ADMITTED_JOBS == _TO_THREAD_ADMITTED_LITERAL
assert HANDOFF_ADMITTED_JOBS == _HANDOFF_ADMITTED_LITERAL

__all__ = [
    "FrontierDispatchBody",
    "FrontierDispatchGenerateBody",
    "FrontierDispatchToThreadBody",
    "ImplementCloseoutBody",
    "TeamDispatchBody",
    "TeamDispatchClaimBody",
    "TeamDispatchClaimReleaseBody",
    "TeamDispatchGenerateBody",
    "TeamDispatchOccupancyBody",
    "TeamDispatchSteerBody",
    "TeamDispatchToThreadBody",
    "TeamHandoffBody",
    "_closeout_dedupe",
    "_dispatch",
    "_normalize_op_body",
    "claim_id_from_generation_options",
    "frontier_dispatch",
    "frontier_router",
    "hop_park_release_requested",
    "implement_closeout",
    "implement_pinned_deliverable",
    "implement_router",
    "team_dispatch",
    "team_handoff",
    "team_router",
]
