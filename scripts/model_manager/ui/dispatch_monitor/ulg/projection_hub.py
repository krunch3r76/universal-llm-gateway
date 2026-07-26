"""Minimal projection hub until ``libs/projection`` lands (todo:projection-channel-libs-primitive)."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from typing import Any

from scripts.model_manager.ui.dispatch_monitor.core.dtos import SupervisorProjection

Subscriber = Callable[[SupervisorProjection], None]


class BroadcastHub:
    """Drop-oldest fan-out for immutable projection snapshots."""

    def __init__(self, *, capacity: int = 8) -> None:
        self._capacity = max(1, capacity)
        self._subscribers: deque[Subscriber] = deque()

    def subscribe(self, callback: Subscriber) -> None:
        self._subscribers.append(callback)

    def publish(self, frame: SupervisorProjection) -> None:
        while len(self._subscribers) > self._capacity:
            self._subscribers.popleft()
        for callback in list(self._subscribers):
            callback(frame)
