"""Per-connection seq watermarks for the v3 §11 subscribe filters."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from scripts.model_manager.ui.dispatch_monitor.ulg.subscribe_filters import LIVE_FILTERS

FILTER_KEYS: tuple[str, ...] = tuple(
    event_filter["signal"] for event_filter in LIVE_FILTERS
)


def filter_key(event_filter: Mapping[str, str]) -> str:
    """Return the stable key for one subscribe filter."""
    return str(event_filter["signal"])


def family_key_for_signal(signal: str) -> str | None:
    """Map a folded signal to the subscribe family it belongs to."""
    if signal.startswith("manage.charter.tick."):
        return "manage.charter.tick.*"
    if signal.startswith("frontier.sdk.") or signal.startswith("pipeline.frontier."):
        return "frontier.sdk.*"
    if signal.startswith("cdp.generate."):
        return "cdp.generate.*"
    if signal == "frontier.poll.hint.issued":
        return "frontier.poll.hint.issued"
    if signal == "system.started":
        return "system.started"
    return None


@dataclass
class ConnectionWatermarks:
    """Independent high-water seq per subscribe connection — no global watermark."""

    _seq: dict[str, int | None] = field(default_factory=lambda: {k: None for k in FILTER_KEYS})

    @classmethod
    def fresh(cls) -> ConnectionWatermarks:
        return cls()

    def get(self, key: str) -> int | None:
        return self._seq.get(key)

    def advance(self, key: str, seq: int | None) -> None:
        if not isinstance(seq, int) or isinstance(seq, bool):
            return
        current = self._seq.get(key)
        if current is None or seq > current:
            self._seq[key] = seq

    def snapshot(self) -> dict[str, int | None]:
        return dict(self._seq)
