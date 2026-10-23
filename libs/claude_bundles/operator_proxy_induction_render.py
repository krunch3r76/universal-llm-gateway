"""K44 hop induction block and successor opener (house 19739, items 5–5f)."""

from __future__ import annotations

INDUCTION_OPENER_TEXT = (
    "induction open: greet me, volunteer what the handoff underweights, then answer my "
    "questions and follow-ups citing a file or turn. We continue until we both agree I am "
    "strongly oriented; then I send the stand-down and you detach."
)


def render_induction_k44_block(*, lane: str) -> str:
    """Render numbered K44 steps for successor and predecessor seats."""
    del lane
    return f"""## Induction (K44)

### Successor (this seat)
1. After act 6 ``TYPE: SEAT_REGISTRATION`` on this lane, do not send a bare stand-down first.
2. ``cse_session(op=followup)`` to the predecessor ``chat_url`` only, ``envelope=free``, ``min_receipt=dom_committed``, with this opener (verbatim): ``{INDUCTION_OPENER_TEXT}``
3. Ask at most three opening questions a file cannot answer (events after this handoff; other holders of the same work; Kaywan words not yet written). There is no cap on follow-ups; the three-question limit covers opening questions only.
4. 10 minutes of silence after a paste ends the exchange as ``no induction``. Passing one million input tokens in one exchange means stop and ask maestro (19627#74).
5. If the predecessor is mid-stream when the opener lands, the opener queues as its next user turn; a reply after the stream ends counts as live.
6. Continue until both seats are satisfied the successor is strongly oriented: say you are oriented, wait for the predecessor to agree, then ``cse_session(op=followup, envelope=stand_down, …)`` with ``min_receipt=dom_committed``. If predecessor detach fails (a:40105), you may detach it only after both rows show different ports.

### Predecessor (prior registration on this lane)
1. Stay live until stand-down; on the successor's opener, greet with the open decision, your judgment, worries, and next move; volunteer first what the handoff underweights.
2. Answer questions citing a file or bus turn; relay Kaywan words after the handoff as FACT turns marked ``successor inherits``.
3. Continue until both seats are satisfied the successor is strongly oriented; agree when the successor says it is oriented; then wait for stand-down and detach (item 4 tape seal is best-effort and does not block detach).
"""


__all__ = ["INDUCTION_OPENER_TEXT", "render_induction_k44_block"]
