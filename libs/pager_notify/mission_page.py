"""Operator-facing pager body shapes — vision/architecture first, not status dumps.

Mission-debrief SOT remains ``cdp-operator-proxy`` § Mission-debrief format.
This module is the mechanical composer for host-side scripts (summons watchdog,
watch scripts) that cannot load that skill at page time.
"""

from __future__ import annotations

from pager_notify.so_what import SMS_BODY_MAX, SMS_SUBJECT_MAX, clip

__all__ = [
    "SUMMONS_ARCH_ONE_LINER",
    "format_mission_awareness_page",
    "format_summons_stop_page",
    "summons_look_ahead",
    "summons_look_back",
]

SUMMONS_ARCH_ONE_LINER = (
    "The summons loop only keeps continuity; the commission's finish line is "
    "what defines mission done — wake without that bind is a false green light."
)

_STOP_LOOK_BACK = {
    "arc_complete": (
        "A commission flag said the episode loop was finished. That flag has been "
        "wrong before when it closed a cleanup slice while the real mission "
        "(SDK align, git branches, borrowed multi-agent flows) was still open."
    ),
    "deadline": (
        "The wall-clock budget for unattended episodes ran out before the "
        "commission declared the three-fold mission finished."
    ),
    "consecutive_failures": (
        "Two launches in a row admitted but never showed a live Cowork session — "
        "the wake path fired, the seat never arrived."
    ),
    "episode_budget": (
        "The episode budget capped further summons before the mission folds "
        "were marked terminal."
    ),
    "commission_seq_not_incremented": (
        "An episode exited without rewriting the next-commission pointer — "
        "the replay guard stopped rather than re-running the same window."
    ),
    "commission_unreadable": (
        "The commission file that tells the loop what 'done' means could not "
        "be read, so the loop refused to guess."
    ),
}

_STOP_LOOK_AHEAD = {
    "arc_complete": (
        "Treat arc_complete as real only if the three folds are terminal in the "
        "commission. Otherwise reopen the mission commission and re-arm the loop — "
        "do not celebrate a sub-arc."
    ),
    "deadline": (
        "Extend the deadline or finish the open folds in an attended window; "
        "the unfinished work is still the mission."
    ),
    "consecutive_failures": (
        "Fix Cowork Auto/approval so the next summon attaches, then re-arm — "
        "the mission did not complete."
    ),
    "episode_budget": (
        "Raise the budget or land the remaining folds in fewer, fatter episodes; "
        "re-arm against the same mission finish line."
    ),
    "commission_seq_not_incremented": (
        "Repair the departing seat's continuity rewrite, then re-arm — "
        "missing pointer is a continuity defect, not mission done."
    ),
    "commission_unreadable": (
        "Restore the commission file, then re-arm. Without it the loop has no "
        "honest finish line."
    ),
}


def summons_look_back(reason: str) -> str:
    return _STOP_LOOK_BACK.get(
        reason,
        f"The overnight episode loop stopped ({reason}) before the mission finish line.",
    )


def summons_look_ahead(reason: str) -> str:
    return _STOP_LOOK_AHEAD.get(
        reason,
        "Read the commission, restore the finish line, and re-arm the loop.",
    )


def format_mission_awareness_page(
    *,
    subject: str,
    vision: str,
    looking_back: str,
    architecture: str,
    looking_ahead: str,
    beyond_bullets: list[str],
    tag: str = "mission-debrief",
) -> tuple[str, str, str]:
    """Compose a mission-class pager body.

    Order is binding: vision → look-back → architecture → look-ahead → Beyond.
    Layman prose — caller owns that; this only structures the slots.
    """
    beyond = _beyond_block(beyond_bullets)
    body = "\n\n".join(
        [
            vision.strip(),
            f"Looking back: {looking_back.strip()}",
            f"Architecture: {architecture.strip()}",
            f"Looking ahead: {looking_ahead.strip()}",
            beyond,
        ]
    )
    return clip(subject, SMS_SUBJECT_MAX), clip(body, SMS_BODY_MAX), tag[:40]


def format_summons_stop_page(
    *,
    reason: str,
    mission: str,
    looking_back: str,
    architecture: str,
    looking_ahead: str,
    beyond_bullets: list[str],
) -> tuple[str, str, str]:
    """Stop/page for the overnight summons loop — always vision-framed."""
    vision = (
        f"ULG's agent fleet is supposed to keep improving how it knows what "
        f"happened and what to do next — without you babysitting the pane. "
        f"This stop is about that mission ({mission.strip() or 'operator-proxy arc'}), "
        f"not a green status light."
    )
    tag = "mission-debrief" if reason == "arc_complete" else "summons-stop"
    subject = clip(f"ULG mission loop stopped — {reason}", SMS_SUBJECT_MAX)
    return format_mission_awareness_page(
        subject=subject,
        vision=vision,
        looking_back=looking_back,
        architecture=architecture,
        looking_ahead=looking_ahead,
        beyond_bullets=beyond_bullets,
        tag=tag,
    )


def _beyond_block(bullets: list[str]) -> str:
    lines = ["Beyond this close:"]
    cleaned = [b.strip().lstrip("- ").strip() for b in bullets if b and b.strip()]
    if not cleaned:
        lines.append("- none")
    else:
        for b in cleaned:
            lines.append(f"- {b}")
    return "\n".join(lines)
