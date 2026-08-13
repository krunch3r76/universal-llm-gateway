"""Carry generated L2 orientation onto the continuity-hop prompt (7119 L2).

The admit-report parity line closes the *authoring* loop — it tells whoever
wrote the directive what bound. It never reaches the successor, which does not
read its predecessor's admit turn. Generating the arrival card into the hop
prompt is the inheritance half of that closure (§AC5 "what else would be
needed").

Generation is best-effort by construction: a hop that cannot be oriented must
still hop, so every failure degrades to an absent block rather than a raised
exception.
"""

from __future__ import annotations

import asyncio
from typing import Any

from universal_logging import get_logger

from services.git_integration_worker.cursor_auto.episode_briefing import (
    fetch_thread_turns,
)
from services.git_integration_worker.cursor_auto.l2_orientation import (
    generate_l2_orientation,
)
from services.git_integration_worker.cursor_auto.queue import AutoJob

logger = get_logger(__name__)

_HEADER = "## Arrival orientation — generated at hop (L2)"

# The lane fetch sits ahead of the CDP commission on a liveness-critical path.
# Orientation is worth a short wait and nothing more.
_LANE_FETCH_TIMEOUT_S = 5.0


def format_resolved_envelope(*, model: str, effort: dict[str, Any]) -> str:
    """What the successor is running as — not what the predecessor's body asked for."""
    return (
        "resolved_envelope: "
        f"model={model} "
        f"requested_effort={effort.get('requested') or 'unset'} "
        f"resolved_effort={effort.get('resolved_effort') or 'unset'} "
        f"wire_effort={effort.get('wire_effort') or 'unset'}"
    )


def compose_orientation_block(
    *,
    envelope_line: str,
    arrival_card: str,
    handoff_prompt: str,
) -> str:
    """Assemble the successor-facing block: envelope first, then live snapshot."""
    return "\n\n".join([_HEADER, envelope_line, arrival_card, handoff_prompt])


async def build_hop_orientation(
    job: AutoJob,
    *,
    model: str,
    effort: dict[str, Any],
) -> dict[str, Any]:
    """Generate the successor's orientation block; never raises."""
    envelope_line = format_resolved_envelope(model=model, effort=effort)
    try:
        async with asyncio.timeout(_LANE_FETCH_TIMEOUT_S):
            turns = await fetch_thread_turns(str(job.thread_id))
        result = generate_l2_orientation(
            thread_id=str(job.thread_id), turns=turns or []
        )
    except Exception as exc:  # orientation is advisory — the hop still hops
        logger.warning(
            "hop orientation generation failed for thread=%s: %s", job.thread_id, exc
        )
        return {
            "generated": False,
            "error": str(exc),
            "block": envelope_line,
            "inheritance_loop_closed": False,
        }
    return {
        "generated": True,
        "block": compose_orientation_block(
            envelope_line=envelope_line,
            arrival_card=result.arrival_card,
            handoff_prompt=result.handoff_prompt,
        ),
        "generated_at": result.generated_at,
        "inheritance_loop_closed": result.inheritance_loop_closed,
        "dropped_sections": list(result.dropped_sections),
    }


def prepend_orientation(body: str, block: str | None) -> str:
    """Put orientation ahead of the directive so it is read before the ask."""
    if not block:
        return body
    return f"{block}\n\n---\n\n{body}"


__all__ = [
    "build_hop_orientation",
    "compose_orientation_block",
    "format_resolved_envelope",
    "prepend_orientation",
]
