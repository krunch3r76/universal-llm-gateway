"""Bus closeout fetch + degraded-reason mapping for skill-suggest dispatch."""

from __future__ import annotations

import httpx
from transport_utils import DEFAULT_AGENT_BUS_URL, make_async_client
from universal_logging import get_logger

from .cursor_sdk_generate import CURSOR_SDK_REPLY_SEAT
from .skill_suggest_dispatch_config import SkillSuggestDispatchConfig
from .skill_suggest_durable_state import LedgerDispatchRow, read_ledger_dispatch_row
from .skill_suggest_worker_waiter import WorkerWaitOutcome

logger = get_logger(__name__)

DegradedReason = str


async def fetch_worker_closeout_body(
    *,
    thread_id: str,
    headers: dict[str, str],
    config: SkillSuggestDispatchConfig,
    after_turn: int = 1,
) -> str | None:
    """Single race-safe bus snapshot after durable terminal (Layer 4)."""
    wait_s = min(
        config.agent_bus_wait_chunk_seconds,
        config.agent_bus_max_wait_seconds,
    )
    async with make_async_client(
        DEFAULT_AGENT_BUS_URL,
        timeout=config.agent_bus_client_timeout_seconds,
    ) as client:
        try:
            resp = await client.get(
                f"/threads/{thread_id}/wait",
                params={
                    "after_turn": after_turn,
                    "wait": 0,
                    "completion": "first_reply_from",
                    "from_agent": CURSOR_SDK_REPLY_SEAT,
                },
                headers=headers,
            )
        except httpx.HTTPError as exc:
            logger.warning("skill_suggest_dispatch bus snapshot transport error: %s", exc)
            return None
        if resp.status_code >= 400:
            logger.warning(
                "skill_suggest_dispatch bus snapshot rejected: status=%s body=%s",
                resp.status_code,
                resp.text[:200],
            )
            return None
        snap = resp.json()
        if not snap.get("complete"):
            return None
        reply_turn = snap.get("qualifying_reply_turn")
        if not isinstance(reply_turn, int):
            return None
        turn_resp = await client.get(
            "/turns/by-number",
            params={"thread": thread_id, "turn_number": reply_turn},
            headers=headers,
        )
        if turn_resp.status_code >= 400:
            return None
        body = turn_resp.json().get("body")
        return body if isinstance(body, str) else None


def map_wait_outcome_to_degraded_reason(
    outcome: WorkerWaitOutcome,
    *,
    ledger: LedgerDispatchRow | None,
    closeout_body: str | None,
    envelope_ok: bool,
) -> DegradedReason | None:
    if outcome.kind == "idle_timeout":
        return "worker_idle_timeout"
    if outcome.kind == "delivery_failed":
        return "worker_no_reply"
    if outcome.kind == "failed" or outcome.kind == "timeout":
        if closeout_body is None:
            return "worker_unreachable"
        if not envelope_ok:
            return "worker_reply_unparseable"
        return None
    if outcome.kind == "completed":
        if closeout_body is None:
            return "worker_no_reply"
        if not envelope_ok:
            return "worker_reply_unparseable"
        return None
    if ledger is not None and ledger.status == "failed":
        return "worker_unreachable"
    return "worker_no_reply"


def load_ledger_snapshot(
    *,
    dispatch_id: str | None,
    execution_id: str,
    thread_id: str,
) -> LedgerDispatchRow | None:
    return read_ledger_dispatch_row(
        dispatch_id=dispatch_id,
        execution_id=execution_id,
        thread_id=thread_id,
    )
