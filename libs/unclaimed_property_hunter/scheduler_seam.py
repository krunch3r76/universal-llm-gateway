"""Scheduler seam for a later twice-yearly SCO load — not a cron.

SCO holder cycles land in bulk twice a year. Wiring a 48h poller is out of
scope. Import :func:`scheduled_entry` from a future job runner; it delegates
to the same one-shot CLI entry and does not sleep or register a timer.
"""

from __future__ import annotations

from unclaimed_property_hunter.cli import main as one_shot_main


def scheduled_entry(argv: list[str] | None = None) -> int:
    """Invoke the one-shot CLI with `argv` (default: sweep --surname required).

    A scheduler may call this; this module never starts a loop or cron.
    """
    return one_shot_main(argv)
