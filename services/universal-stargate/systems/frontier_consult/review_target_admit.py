"""Stargate-local boundary for review-target admit (isolates hook import failures)."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_INFRA_FIX = (
    "Retry after hub git is healthy; if the lane worktree was removed, use "
    "fs op=copy from workspaces to cortex and cite the copy."
)


async def review_target_api_admit(
    request_id: str,
    prompt_text: str,
    frontier_error: type[Any],
) -> tuple[str, dict[str, Any] | None]:
    """Lazy-load review hook; map import failures to 422 infra."""
    try:
        from review_layer.hook import review_target_api_admit as _hook_admit
    except Exception as exc:
        logger.exception("review_target hook import failed")
        raise frontier_error(
            request_id=request_id,
            field="prompt",
            reason="review target module unavailable",
            status_code=422,
            code="REVIEW_TARGET_UNREACHABLE",
            details={"kind": "infra", "fix_hint": _INFRA_FIX},
        ) from exc
    return await _hook_admit(request_id, prompt_text, frontier_error)
