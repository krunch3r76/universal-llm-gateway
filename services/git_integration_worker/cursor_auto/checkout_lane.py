"""Resolve checkout-isolation lane for cursor-auto nested cursor-sdk POSTs."""

from __future__ import annotations

from typing import Literal

from universal_logging import get_logger

from services.git_integration_worker.cursor_auto.queue import AutoJob

Lane = Literal["A", "B"]
CheckoutLaneReason = Literal[
    "read_only",
    "explicit",
    "auto_implement_class",
    "auto_named_a_non_implement",
    "scope_refused",
]

_IMPLEMENT_CLASS_CONTRACTS = frozenset({"implement", "verify"})

logger = get_logger(__name__)


def may_nest_under(
    *,
    holder_lane: Lane | None,
    holder_thread_id: str | None,
    job: AutoJob,
) -> bool:
    """True when nesting under the live write-lease holder is allowed."""
    if holder_lane != "B":
        return False
    if not holder_thread_id or holder_thread_id != job.thread_id:
        return False
    return True


def resolve_nested_checkout_lane(
    job: AutoJob,
    *,
    read_only: bool,
) -> tuple[Lane, CheckoutLaneReason]:
    """Choose ``lane`` stamped on nested SDK POST from Auto job fields.

    Order: ``read_only`` → omit/A (no B stamp); explicit ``job.lane``;
    implement-class (``implement``, ``verify``) → B; else A.
    """
    if read_only:
        logger.info(
            "cursor-auto checkout lane=A reason=read_only job=%s contract=%s",
            job.job_id,
            job.contract,
        )
        return "A", "read_only"

    explicit = (job.lane or "").strip().upper()
    if explicit in {"A", "B"}:
        lane: Lane = "A" if explicit == "A" else "B"
        logger.info(
            "cursor-auto checkout lane=%s reason=explicit job=%s contract=%s",
            lane,
            job.job_id,
            job.contract,
        )
        return lane, "explicit"

    contract = (job.contract or "").strip().lower()
    if contract in _IMPLEMENT_CLASS_CONTRACTS:
        logger.info(
            "cursor-auto checkout lane=B reason=auto_implement_class job=%s contract=%s",
            job.job_id,
            contract,
        )
        return "B", "auto_implement_class"

    logger.info(
        "cursor-auto checkout lane=A reason=auto_named_a_non_implement job=%s contract=%s",
        job.job_id,
        contract or "(unset)",
    )
    return "A", "auto_named_a_non_implement"


__all__ = [
    "CheckoutLaneReason",
    "Lane",
    "may_nest_under",
    "resolve_nested_checkout_lane",
]
