"""POST lane↔branch association to agent-bus on Lane-B mint."""

from __future__ import annotations

import logging
import os

from transport_utils import DEFAULT_AGENT_BUS_URL, make_async_client

logger = logging.getLogger(__name__)


async def associate_lane_branch(*, thread_id: str, branch_name: str) -> bool:
    """POST the lane↔branch association to agent-bus. Never raises."""
    tid = (thread_id or "").strip()
    branch = (branch_name or "").strip()
    if not tid or not branch:
        return False
    token = os.environ.get("AGENT_BUS_TOKEN", "").strip()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        async with make_async_client(DEFAULT_AGENT_BUS_URL, timeout=10.0) as client:
            resp = await client.post(
                f"/threads/{tid}/branch-associate",
                json={"branch_name": branch},
                headers=headers,
            )
        return 200 <= resp.status_code < 300
    except Exception:
        logger.warning(
            "associate_lane_branch failed thread_id=%s branch_name=%s",
            tid,
            branch,
            exc_info=True,
        )
        return False
