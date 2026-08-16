"""Best-effort ``land_required`` tag on the owning bus thread."""

from __future__ import annotations

import os

from transport_utils import DEFAULT_AGENT_BUS_URL, make_sync_client
from universal_logging import get_logger

logger = get_logger(__name__)

LAND_REQUIRED_TAG = "land_required"


def _patch_thread_tags(
    *,
    thread_id: str,
    add_tags: list[str] | None = None,
    remove_tags: list[str] | None = None,
) -> bool:
    """PATCH agent-bus thread tags; never raises."""
    tid = (thread_id or "").strip()
    if not tid or not tid.isdigit():
        return False
    payload: dict[str, list[str]] = {}
    if add_tags:
        payload["add_tags"] = add_tags
    if remove_tags:
        payload["remove_tags"] = remove_tags
    if not payload:
        return False
    token = os.environ.get("AGENT_BUS_TOKEN", "").strip()
    if not token:
        logger.warning(
            "branch-debt tag patch skipped: AGENT_BUS_TOKEN not configured thread=%s",
            tid,
        )
        return False
    try:
        with make_sync_client(DEFAULT_AGENT_BUS_URL, timeout=10.0) as client:
            resp = client.patch(
                f"/threads/{tid}",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
        if resp.status_code not in (200, 201):
            logger.warning(
                "branch-debt tag patch failed thread=%s status=%s tags=%s",
                tid,
                resp.status_code,
                payload,
            )
            return False
    except Exception as exc:
        logger.warning("branch-debt tag patch error thread=%s: %s", tid, exc)
        return False
    return True


def add_land_required_tag(*, thread_id: str | None) -> bool:
    """Stamp ``land_required`` when a branch debt opens."""
    return _patch_thread_tags(thread_id=thread_id or "", add_tags=[LAND_REQUIRED_TAG])


def remove_land_required_tag(*, thread_id: str | None) -> bool:
    """Clear ``land_required`` after branch discharge."""
    return _patch_thread_tags(
        thread_id=thread_id or "",
        remove_tags=[LAND_REQUIRED_TAG],
    )
