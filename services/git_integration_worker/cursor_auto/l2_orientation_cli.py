"""CLI entry — generate L2 arrival card + handoff_prompt for a lane thread."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from services.git_integration_worker.cursor_auto.episode_briefing import fetch_thread_turns
from services.git_integration_worker.cursor_auto.l2_orientation import generate_l2_orientation


async def _run(thread_id: str) -> dict:
    turns = await fetch_thread_turns(thread_id)
    if turns is None:
        turns = []
    result = generate_l2_orientation(thread_id=thread_id, turns=turns)
    return {
        "thread_id": result.thread_id,
        "generated_at": result.generated_at,
        "constitution": result.constitution,
        "inheritance_loop_closed": result.inheritance_loop_closed,
        "dropped_sections": result.dropped_sections,
        "sources": [
            {
                "slice": s.slice_name,
                "source": s.source,
                "queryable": s.queryable,
                "note": s.note,
            }
            for s in result.sources
        ],
        "arrival_card": result.arrival_card,
        "handoff_prompt": result.handoff_prompt,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate L2 orientation artifacts")
    parser.add_argument("thread_id", nargs="?", default="6655")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit full JSON (arrival_card + handoff_prompt)",
    )
    args = parser.parse_args(argv)
    payload = asyncio.run(_run(args.thread_id))
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print("=== ARRIVAL CARD ===")
        print(payload["arrival_card"])
        print("\n=== HANDOFF PROMPT ===")
        print(payload["handoff_prompt"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
