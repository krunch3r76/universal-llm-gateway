#!/usr/bin/env python
"""Page when the liaison ticker stops ticking — a dead clock is otherwise invisible.

On 2026-09-14 the gear-3 ticker crash-looped on a ValueError and nobody learned
about it for ~14 minutes. Nothing was watching, and the two mechanisms one would
reach for first do not work here:

``OnFailure=`` never fires.
    ``liaison-ticker-10479.service`` sets ``Restart=on-failure`` with
    ``RestartSec=60``, while the start-limit is ``StartLimitBurst=5`` inside
    ``StartLimitIntervalSec=10``. Five restarts cannot land inside a ten-second
    window when they are a minute apart, so the unit never reaches ``failed``
    and an ``OnFailure=`` hook is dead code. The incident bears this out: the
    clock was down ~14 minutes at ``NRestarts=8`` while the unit still reported
    ``active``/``activating`` throughout.

Unit state is the wrong question anyway.
    ``ActiveState=active`` is true of a healthy ticker, a hung ticker, and a
    ticker in restart backoff. What the house actually depends on is that ticks
    are *happening*, so this watchdog reads progress (``last_tick_at``) rather
    than liveness of a process.

It therefore runs as a separate timer-driven observer: a watchdog inside the
ticker cannot report its own death. Paging reuses the existing charter SOS path
(``pager_notify.sos``), which already carries once-per-root dedupe, instead of
minting a parallel alert channel.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from pager_notify.sos import claim_tick_sos, notify_tick_sos

_REPO = Path(__file__).resolve().parent.parent
_DEFAULT_STATE = _REPO / "tmp" / "watchers" / "liaison-{root}.tick.json"

# ~160s observed mean cadence (1619 ticks over ~3 days), so this is roughly four
# missed ticks — long enough not to fire on one slow bus poll, short enough to
# beat the 14-minute blind window that motivated the watchdog.
_DEFAULT_STALE_S = 600.0


class TickerStatus:
    """Whether the clock is still moving, and the evidence for saying so."""

    def __init__(self, *, healthy: bool, reason: str, detail: str, age_s: float | None):
        self.healthy = healthy
        self.reason = reason
        self.detail = detail
        self.age_s = age_s


def _parse_last_tick(raw: str) -> datetime | None:
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def evaluate(state_path: Path, *, stale_after_s: float, now: datetime) -> TickerStatus:
    """Classify the ticker from its own progress record.

    A missing or unreadable state file counts as unhealthy: the watchdog exists
    precisely for the cases where the ticker is not in a position to complain.
    """
    if not state_path.is_file():
        return TickerStatus(
            healthy=False,
            reason="state_missing",
            detail=f"no tick state at {state_path}",
            age_s=None,
        )
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return TickerStatus(
            healthy=False,
            reason="state_unreadable",
            detail=f"{type(exc).__name__}: {exc}",
            age_s=None,
        )

    last_tick = _parse_last_tick(state.get("last_tick_at", ""))
    if last_tick is None:
        return TickerStatus(
            healthy=False,
            reason="no_last_tick",
            detail="state has no parseable last_tick_at",
            age_s=None,
        )

    age_s = (now - last_tick).total_seconds()
    ticks = state.get("ticks")
    if age_s > stale_after_s:
        return TickerStatus(
            healthy=False,
            reason="ticker_stale",
            detail=(
                f"last tick {age_s:.0f}s ago (limit {stale_after_s:.0f}s), "
                f"ticks={ticks}"
            ),
            age_s=age_s,
        )
    return TickerStatus(
        healthy=True,
        reason="ok",
        detail=f"last tick {age_s:.0f}s ago, ticks={ticks}",
        age_s=age_s,
    )


async def _page(root: str, status: TickerStatus) -> bool:
    if not claim_tick_sos(f"{root}-ticker-watchdog", status.reason):
        return False
    return await notify_tick_sos(
        root_id=root,
        reason=f"CLOCK DOWN: {status.reason}",
        detail=status.detail,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="10479")
    parser.add_argument("--state-file")
    parser.add_argument(
        "--stale-after",
        type=float,
        default=float(os.environ.get("LIAISON_TICKER_STALE_S", _DEFAULT_STALE_S)),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="classify and report without paging",
    )
    args = parser.parse_args(argv)

    state_path = (
        Path(args.state_file)
        if args.state_file
        else Path(str(_DEFAULT_STATE).format(root=args.root))
    )
    status = evaluate(
        state_path,
        stale_after_s=args.stale_after,
        now=datetime.now(UTC),
    )
    print(f"{'ok' if status.healthy else 'STALE'} {status.reason}: {status.detail}")

    if status.healthy or args.dry_run:
        return 0
    paged = asyncio.run(_page(args.root, status))
    print(f"paged={paged} (False = suppressed by once-per-root dedupe)")
    # Exit 0 either way: a paged alert is a successful watchdog run, and a
    # non-zero exit would only add a second, noisier failure signal on the timer.
    return 0


if __name__ == "__main__":
    sys.exit(main())
