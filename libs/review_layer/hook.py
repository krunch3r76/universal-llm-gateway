"""API-role admit hook: lane resolution, async git, and Stargate error mapping."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from .detect import (
    detect_review_targets,
    implicit_lane_thread_ids,
    resolve_primary_target,
)
from .materialize import (
    format_review_target_block,
    resolve_review_target,
)

logger = logging.getLogger(__name__)


class ReviewLayerAdmitError(Exception):
    """Maps to FrontierEndpointError at the Stargate boundary."""

    def __init__(
        self,
        *,
        reason: str,
        kind: str,
        fix_hint: str,
        field: str = "prompt",
    ) -> None:
        self.reason = reason
        self.kind = kind
        self.fix_hint = fix_hint
        self.field = field
        super().__init__(reason)


_TARGET_FIX = (
    "Commit to the lane branch, add line-start review_thread: <digits> and optional "
    "review_head: / review_base:, or if the worktree was removed after land use "
    "fs op=copy (workspaces to cortex) and cite the copy in the prompt."
)

_AMBIGUOUS_FIX = (
    "Add a single line-start review_thread: <digits> naming the lane to review."
)

_INFRA_FIX = (
    "Retry after hub git is healthy; if the lane worktree was removed, use "
    "fs op=copy from workspaces to cortex and cite the copy."
)


def _resolve_sync(prompt_text: str) -> tuple[str, dict[str, Any] | None]:
    from review_layer.git_io import ReviewLayerInfraError, ReviewTargetUnreachable

    detection = detect_review_targets(prompt_text)
    implicit = implicit_lane_thread_ids(detection)
    if len(implicit) > 1:
        raise ReviewLayerAdmitError(
            reason="multiple implicit lane targets",
            kind="ambiguous",
            fix_hint=_AMBIGUOUS_FIX,
        )

    target = resolve_primary_target(detection)
    if target is None:
        return prompt_text, None

    try:
        resolved = resolve_review_target(
            thread_id=target.thread_id,
            head_ref=target.head,
            base_ref=target.base,
        )
    except ReviewTargetUnreachable as exc:
        raise ReviewLayerAdmitError(
            reason=exc.reason[:120],
            kind="target",
            fix_hint=_TARGET_FIX,
        ) from exc
    except ReviewLayerInfraError as exc:
        raise ReviewLayerAdmitError(
            reason=exc.reason[:120],
            kind="infra",
            fix_hint=_INFRA_FIX,
        ) from exc

    if resolved is None:
        return prompt_text, None

    block = format_review_target_block(
        thread_id=resolved["thread"],
        branch=resolved["branch"],
        base_sha=resolved["base_sha"],
        head_sha=resolved["head_sha"],
    )
    payload = {
        "thread": resolved["thread"],
        "branch": resolved["branch"],
        "base_sha": resolved["base_sha"],
        "head_sha": resolved["head_sha"],
    }
    return prompt_text + block, payload


async def apply_review_target_admit(
    *,
    prompt_text: str,
) -> tuple[str, dict[str, Any] | None]:
    """Resolve lane review target and append ``## review_target`` when needed."""
    try:
        return await asyncio.to_thread(_resolve_sync, prompt_text)
    except ReviewLayerAdmitError:
        raise
    except Exception as exc:
        logger.exception("review_target admit failed")
        raise ReviewLayerAdmitError(
            reason="review target resolution failed",
            kind="infra",
            fix_hint=_INFRA_FIX,
        ) from exc


async def review_target_api_admit(
    request_id: str,
    prompt_text: str,
    frontier_error: type[Any],
) -> tuple[str, dict[str, Any] | None]:
    """Stargate entry: lazy hook load, mapped 422, preserves ``request_id`` on errors."""
    try:
        prompt, payload = await apply_review_target_admit(prompt_text=prompt_text)
        return prompt, payload
    except ReviewLayerAdmitError as exc:
        raise frontier_error(
            request_id=request_id,
            field=exc.field,
            reason=exc.reason,
            status_code=422,
            code="REVIEW_TARGET_UNREACHABLE",
            details={"kind": exc.kind, "fix_hint": exc.fix_hint},
        ) from exc
    except Exception as exc:
        if exc.__class__ is frontier_error:
            raise
        logger.exception("review_target bridge failed")
        raise frontier_error(
            request_id=request_id,
            field="prompt",
            reason="review target module unavailable",
            status_code=422,
            code="REVIEW_TARGET_UNREACHABLE",
            details={"kind": "infra", "fix_hint": _INFRA_FIX},
        ) from exc
