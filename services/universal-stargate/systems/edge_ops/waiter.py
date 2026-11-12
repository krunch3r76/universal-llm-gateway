"""Advisory wake-ups for the two long-polls. Never authority.

A lease long-poll and a caller await both want to return the moment the store
changes rather than at the end of their budget. This registry is how they
learn that something changed; what changed is always re-read from the sqlite
authority afterwards. Lose every wake-up and the substrate still behaves
correctly, only less promptly — which is why a restarted process needs no
handover here.

Two fixed channels keep the registry bounded: ``queued`` fires when an op
becomes leasable, ``transition`` when any op changes state. Waiters re-check
the store on each wake, so a coarse channel costs a reread, never a miss.
"""

from __future__ import annotations

import asyncio

QUEUED_CHANNEL = "queued"
TRANSITION_CHANNEL = "transition"

_CHANNELS = (QUEUED_CHANNEL, TRANSITION_CHANNEL)


class WakeRegistry:
    """Monotonic per-channel epochs behind one condition variable."""

    def __init__(self) -> None:
        self._condition = asyncio.Condition()
        self._epochs: dict[str, int] = dict.fromkeys(_CHANNELS, 0)

    def epoch(self, channel: str) -> int:
        """Current epoch for *channel*; capture it before waiting on it."""
        return self._epochs.get(channel, 0)

    async def notify(self, channel: str) -> None:
        """Bump *channel* and wake everyone parked on it."""
        async with self._condition:
            self._epochs[channel] = self._epochs.get(channel, 0) + 1
            self._condition.notify_all()

    async def wait(self, channel: str, *, since: int, timeout: float) -> bool:
        """Wait until *channel* moves past *since*. False means the budget ran out."""
        if self.epoch(channel) > since:
            return True
        if timeout <= 0:
            return False
        try:
            await asyncio.wait_for(
                self._wait_past(channel, since),
                timeout=timeout,
            )
        except TimeoutError:
            return False
        return True

    async def _wait_past(self, channel: str, since: int) -> None:
        """Park on the condition until the channel epoch passes *since*."""
        async with self._condition:
            await self._condition.wait_for(lambda: self.epoch(channel) > since)
