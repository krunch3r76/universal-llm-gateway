"""K44 hop induction block and successor opener (house 19739, items 5–5f)."""

from __future__ import annotations

INDUCTION_OPENER_TEXT = (
    "induction open: greet me, then walk what is outstanding before anything else — open "
    "decisions, acts in flight, promises to Kaywan, and anything you said in chat that is "
    "not yet written in the handoff or on the bus; then answer my questions and follow-ups "
    "citing a file or turn. We continue until we both agree I am strongly oriented; then I "
    "send the stand-down and you detach."
)


def render_induction_k44_block(*, lane: str) -> str:
    """Render numbered K44 steps for successor and predecessor seats."""
    del lane
    return f"""## Induction (K44)

### Successor (this seat)
1. After act 6 ``TYPE: SEAT_REGISTRATION`` on this lane, do not send a bare stand-down first.
2. Before the opener, ``cse_session(op=harvest, chat_url=<predecessor chat_url>, source=chat, limit=12, after_turn=1)`` (or ``after_turn=<predecessor first reply turn>`` when that is not turn 1) and read the tail; aim the three opening questions at gaps between that tail and the handoff. Then ``cse_session(op=followup)`` to the predecessor ``chat_url`` only, ``envelope=free``, ``min_receipt=dom_committed``, with this opener (verbatim): ``{INDUCTION_OPENER_TEXT}``
3. Ask at most three opening questions a file cannot answer (events after this handoff; other holders of the same work; Kaywan words not yet written). There is no cap on follow-ups; the three-question limit covers opening questions only.
4. 10 minutes of silence after a paste ends the exchange as ``no induction``. Passing one million input tokens in one exchange means stop and ask maestro (19627#74).
5. If the predecessor is mid-stream when the opener lands, the opener queues as its next user turn; a reply after the stream ends counts as live. The predecessor's walk and answers arrive in this chat as a pasted turn; harvest its window only if nothing arrives within 10 minutes.
6. Continue until both seats are satisfied the successor is strongly oriented: say you are oriented, wait for the predecessor to agree and to confirm its outstanding list is written (handoff or FACT turns), then ``cse_session(op=followup, envelope=stand_down, …)`` with ``min_receipt=dom_committed``. If predecessor detach fails (a:40105), you may detach it only after both rows show different ports.

### Predecessor (prior registration on this lane)
1. Stay live until stand-down; on the successor's opener, first walk what is outstanding — open decisions, acts in flight, promises to Kaywan, and everything said in chat but not yet written — and write each unwritten item into the standing handoff or a FACT turn before answering questions; then give your judgment, worries, and next move.
2. Deliver the walk and every answer into the successor's chat with ``cse_session(op=followup, chat_url=<successor chat_url from its SEAT_REGISTRATION on this lane>, envelope=free, min_receipt=dom_committed)``; a reply left only in your own window is not delivered. Cite a file or bus turn for each answer, and relay Kaywan words after the handoff as FACT turns marked ``successor inherits``.
3. Continue until both seats are satisfied the successor is strongly oriented; agree when the successor says it is oriented; then wait for stand-down and detach (item 4 tape seal is best-effort and does not block detach).
"""


__all__ = ["INDUCTION_OPENER_TEXT", "render_induction_k44_block"]
