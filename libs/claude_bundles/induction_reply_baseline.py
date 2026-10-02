"""Baseline for the work reply after a CDP skill-induction turn.

a:37267: ``purpose=ask`` sealed the induction acknowledgement ("no substantive
question") because the reply wait's ``before`` snapshot was taken before
``send_prompt`` submitted ``Use the {slug} skill``. That first idle assistant
turn satisfies ``cur_n > base_n``. The work body, pasted while that turn was
still in flight or after the seal, never became the harvested answer.

The induction turn is still the delivery receipt (panel, then idle). The
work-reply wait must start from the harvest of that idle turn.
"""

from __future__ import annotations

from typing import Any

# Idle budget for the induction acknowledgement only. Streaming, Stop, and
# tool_pause refresh the deadline inside ``wait_assistant_reply`` (24666).
INDUCTION_REPLY_IDLE_S = 120


def work_reply_before(
    caller_before: dict[str, Any],
    induction_baseline: dict[str, Any] | None,
) -> dict[str, Any]:
    """Baseline the work-reply wait must use.

    ``induction_baseline`` is the idle harvest of the skill-load turn.
    Absent when this send had no induction turn.
    """
    if induction_baseline is not None:
        return induction_baseline
    return caller_before


async def capture_induction_reply_baseline(
    page: Any, *, before: dict[str, Any]
) -> dict:
    """Return the harvest once the induction turn is a new idle assistant message.

    ``require_review_verdict`` stays false here. The skill-load turn has no
    verdict by design; the outer wait still demands one on the next turn
    when ``purpose=review``.
    """
    from claude_bundles.chat_reply_wait import wait_assistant_reply

    return await wait_assistant_reply(
        page,
        before=before,
        timeout_s=INDUCTION_REPLY_IDLE_S,
        poll_ms=500,
        require_review_verdict=False,
    )
