"""Resolve ``nest_under`` for cursor-auto nested SDK dispatches."""

from __future__ import annotations

from typing import Any

from universal_logging import get_logger

from services.git_integration_worker.cursor_auto.checkout_lane import (
    Lane,
    may_nest_under,
)
from services.git_integration_worker.cursor_auto.gate_serialize import (
    prefer_dispatch_over_park,
)
from services.git_integration_worker.cursor_auto.handler_terminal import (
    terminal_needs_attended,
)
from services.git_integration_worker.cursor_auto.queue import AutoJob
from services.git_integration_worker.cursor_bus import CursorBusClient
from services.git_integration_worker.cursor_dispatch_ledger import (
    CursorDispatchLedger,
)
from services.git_integration_worker.cursor_sdk_capacity_invariant import (
    resolve_admit_lane,
)

logger = get_logger(__name__)

_IMPLEMENT_CLASS_CONTRACTS = frozenset({"implement", "verify"})


def _holder_context(holder_dispatch_id: str) -> tuple[Lane | None, str | None]:
    """Return admit lane and thread for a live write-lease holder dispatch."""
    with CursorDispatchLedger.instance()._connect() as conn:
        row = conn.execute(
            "SELECT thread_id, record_json, lease_key, source_repo "
            "FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (holder_dispatch_id,),
        ).fetchone()
    if row is None:
        return None, None
    lane = resolve_admit_lane(
        record_json=row["record_json"] or "{}",
        lease_key=row["lease_key"],
        source_repo=row["source_repo"],
    )
    if lane == "unknown":
        lane = "A"
    return lane, str(row["thread_id"] or "")


async def resolve_nest_under(
    job: AutoJob,
    *,
    client: CursorBusClient,
    queue: Any,
    gate_plan: dict[str, Any],
    work_bounded: bool,
    contract: str,
) -> str | None | dict[str, Any]:
    """Resolve the park parent for ``nest_park``; a dict means terminal refusal."""
    if gate_plan["action"] != "nest_park":
        return None

    snap = CursorDispatchLedger.instance().lease_snapshot()
    holder_dispatch_id = snap.get("holder_dispatch_id")
    if not holder_dispatch_id:
        replan = prefer_dispatch_over_park(
            {**gate_plan, "action": "in_seat", "reason": "nest_park_without_holder"},
            work_bounded=work_bounded,
        )
        gate_plan.update(replan)
        if replan["action"] == "dispatch_now":
            return None
        return await terminal_needs_attended(
            job,
            client=client,
            queue=queue,
            reason="nest_park_without_holder",
            gate_plan=gate_plan,
        )

    holder_id = str(holder_dispatch_id)
    holder_lane, holder_thread_id = _holder_context(holder_id)
    normalized_contract = (contract or "").strip().lower()
    if normalized_contract in _IMPLEMENT_CLASS_CONTRACTS and not may_nest_under(
        holder_lane=holder_lane,
        holder_thread_id=holder_thread_id,
        job=job,
    ):
        logger.info(
            "cursor-auto nest_under refused holder=%s holder_lane=%s "
            "holder_thread=%s job=%s contract=%s",
            holder_id,
            holder_lane,
            holder_thread_id,
            job.job_id,
            normalized_contract,
        )
        replan = prefer_dispatch_over_park(
            {
                **gate_plan,
                "action": "dispatch_now",
                "reason": "nest_under_refused_foreign_lane_a",
            },
            work_bounded=work_bounded,
        )
        gate_plan.update(replan)
        return None

    if not may_nest_under(
        holder_lane=holder_lane,
        holder_thread_id=holder_thread_id,
        job=job,
    ):
        logger.info(
            "cursor-auto nest_under refused holder=%s holder_lane=%s "
            "holder_thread=%s job=%s",
            holder_id,
            holder_lane,
            holder_thread_id,
            job.job_id,
        )
        replan = prefer_dispatch_over_park(
            {
                **gate_plan,
                "action": "dispatch_now",
                "reason": "nest_under_refused",
            },
            work_bounded=work_bounded,
        )
        gate_plan.update(replan)
        return None

    return holder_id


__all__ = ["resolve_nest_under"]
