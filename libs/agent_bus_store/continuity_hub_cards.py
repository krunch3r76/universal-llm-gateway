"""Minimal continuity card bodies for priority ``role:root`` hub mints.

Used when seeding ``document:{root}-continuity`` for lanes that lack a card
before the first consolidate-continuity fold. Authority table:
``cortex://notes/system/specs/continuity-lane-consolidation-opus.md`` §1.
"""

from __future__ import annotations

from typing import Any

_CARD_HEADER = """\
# {title}

Catch-up file for **agent-bus:{root}** (`{slug}`).

## Stance
Use the `ulg-for-llms` skill.

## Why this house
{why}

## Objective
{objective}

## Runbooks
- _None yet._

## Rules
- _None yet._

## Sidecars
- _None yet._

## Scratchboards
- _None yet._

## House
document:{root}-continuity
"""

PRIORITY_HUB_CARDS: dict[str, dict[str, str]] = {
    "9740": {
        "title": "9740 perps trader — house",
        "slug": "perps-trader-lighter",
        "why": (
            "Perps trader v1 — wake/intent/fire_path; nested git scripts.local. "
            "Not 9582 algo/recycle authority."
        ),
        "objective": (
            "**Mission:** no-fire perps trader prototype — book+arms+sit until named fire.\n"
            "**In:** PERPS_TRADER_FIRE, fire_path, intent/wake law, scripts.local/trader.\n"
            "**Out:** treating 9740 as algo recycle authority; leftover POST paths on 9582."
        ),
    },
    "10327": {
        "title": "10327 perps trader continuity — house",
        "slug": "perps-trader-continuity",
        "why": (
            "Monitor/continuity lane for perps trader v1 — WORK closeouts and "
            "autopilot wake relay."
        ),
        "objective": (
            "**Mission:** relay WORK closeouts and autopilot wake for perps trader v1.\n"
            "**In:** CLOSEOUT verification, cursor-auto wake relay.\n"
            "**Out:** money binds; treating as primary trader execution root (see 9740)."
        ),
    },
}


def render_priority_card(root_thread: str) -> str | None:
    """Return markdown card body for a known priority root, else None."""
    profile = PRIORITY_HUB_CARDS.get(root_thread)
    if profile is None:
        return None
    return _CARD_HEADER.format(root=root_thread, **profile)


def hub_card_uri(root_thread: str) -> str:
    return f"cortex://notes/system/threads/{root_thread}-continuity.md"


def entity_create_payload(
    root_thread: str, *, card_sha256: str | None = None
) -> dict[str, Any]:
    """``entity_create`` arguments for a priority hub after the card write."""
    profile = PRIORITY_HUB_CARDS[root_thread]
    summary = profile["objective"].split("\n", 1)[0].removeprefix("**Mission:** ")
    return {
        "id": f"document:{root_thread}-continuity",
        "type": "document",
        "name": profile["title"],
        "description": f"Mission: {summary} agent-bus:{root_thread}",
        "source_uri": hub_card_uri(root_thread),
        **({"content_hash": card_sha256} if card_sha256 else {}),
        "duplicate_name_ok": True,
    }
