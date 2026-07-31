"""Reject ``path-sim`` inside ``attributes.required_skills`` (a:27431).

``todo_ulg.mdc`` § Domain → default ``required_skills`` names the floor as
``architecture-invariants`` + ``ulg-architecture`` with per-domain additions.
``path-sim`` is a choke-point cue for question/solution-space consults, not a
leaf ``required_skills`` entry — stuffing it into codework todos caused CDP
consults to inherit and inline a skill whose body excludes codework.

Fail loud (422). No silent strip. Not a general skill allowlist (a:27430).
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status

_PATH_SIM_SLUG = "path-sim"
_ERROR_CODE = "required_skills_path_sim_rejected"


def reject_path_sim_in_required_skills(skills: Any) -> None:
    """Raise 422 when ``path-sim`` appears in a ``required_skills`` list.

    Non-list values are ignored here — ``validate_distilled_attributes`` already
    owns implement-lane shape rejects for non-list / empty lists.
    """
    if not isinstance(skills, list):
        return
    if _PATH_SIM_SLUG not in skills and "agent_skill:path-sim" not in skills:
        return
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail={
            "error": _ERROR_CODE,
            "attribute": "required_skills",
            "rejected_slug": _PATH_SIM_SLUG,
            "message": (
                "path-sim must not appear in attributes.required_skills "
                "(todo_ulg.mdc § Domain → default required_skills floor; "
                "path-sim is a choke-point cue, not a leaf skill entry; "
                "a:27431)"
            ),
        },
    )


__all__ = ["reject_path_sim_in_required_skills", "_ERROR_CODE", "_PATH_SIM_SLUG"]
