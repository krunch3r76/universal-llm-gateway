"""Capture the idle harvest after a CDP skill-induction turn.

Used only by ``send_prompt`` when ``await_induction_reply=True``. The outer
``project_ask*`` wait is anchored on the sealed prompt marker, not this baseline.
"""

from __future__ import annotations

from typing import Any

INDUCTION_REPLY_IDLE_S = 120


async def capture_induction_reply_baseline(
    page: Any, *, before: dict[str, Any]
) -> dict:
    """Return the harvest once the induction turn is a new idle assistant message."""
    from claude_bundles.chat_reply_wait import wait_assistant_reply

    return await wait_assistant_reply(
        page,
        before=before,
        timeout_s=INDUCTION_REPLY_IDLE_S,
        poll_ms=500,
        require_review_verdict=False,
    )
