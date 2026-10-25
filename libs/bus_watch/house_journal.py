"""POST client for house action journal appends on the agent bus."""

from __future__ import annotations

from transport_utils import DEFAULT_AGENT_BUS_URL, make_sync_client
from universal_logging import get_logger

from bus_watch.digest_budget import agent_bus_bearer_headers

logger = get_logger("bus_watch.house_journal")


def append_house_action(
    root_thread_id: str,
    kind: str,
    actor: str,
    action_why: str,
) -> bool:
    """Append one row; fail-open (log and return False on transport errors)."""
    root = str(root_thread_id or "").strip()
    why = str(action_why or "").strip()
    if not root or not why:
        return False
    headers = agent_bus_bearer_headers()
    if not headers:
        logger.warning("house journal append skipped: no agent-bus bearer token")
        return False
    payload = {
        "root_thread_id": root,
        "kind": str(kind),
        "actor": str(actor),
        "action_why": why,
    }
    try:
        with make_sync_client(DEFAULT_AGENT_BUS_URL, timeout=10.0) as client:
            resp = client.post("/house-actions", json=payload, headers=headers)
        if resp.status_code >= 400:
            logger.warning(
                "house journal append refused root=%s status=%s body=%s",
                root,
                resp.status_code,
                resp.text[:200],
            )
            return False
    except Exception:
        logger.warning(
            "house journal append failed root=%s",
            root,
            exc_info=True,
        )
        return False
    return True


__all__ = ["append_house_action"]
