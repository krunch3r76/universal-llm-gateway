"""ES worker-terminal backfill for LIVE SDK rows (G4b completed-present clear).

Fold stays pure: SdkFold only terminalizes on worker/lifecycle signals. When the
board missed a terminal during seed or live apply, this layer synthesizes
``EventRecord``s from Event Service with ``PROVENANCE_RECONCILED`` and replays
them through ``apply`` — never inventing ``terminal_ms`` from lease release.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from scripts.model_manager.ui.dispatch_monitor.core.folds.sdk import SdkFold
from scripts.model_manager.ui.dispatch_monitor.core.protocols import EventRecord
from scripts.model_manager.ui.dispatch_monitor.ulg.event_query import (
    signal_events,
    worker_terminals_for_dispatch,
)
from scripts.model_manager.ui.dispatch_monitor.ulg.reconcile_events import (
    events_from_es_worker_terminals,
)

_DEFAULT_MAX_IDS = 32
_DEFAULT_LOOKBACK_MINUTES = 24 * 60


def live_dispatch_ids(sdk: SdkFold) -> list[str]:
    """Return dispatch ids still LIVE (``terminal_ms is None``) in fold state."""
    return sorted(
        state.dispatch_id
        for state in sdk.dispatches.values()
        if state.terminal_ms is None
    )


def lease_released_without_terminal_ids(sdk: SdkFold) -> list[str]:
    """LIVE rows flagged by G4 lease-without-terminal attention."""
    return sorted(
        state.dispatch_id
        for state in sdk.dispatches.values()
        if state.terminal_ms is None and state.lease_released_without_terminal
    )


def _ordered_backfill_ids(
    live_dispatch_ids: Sequence[str],
    *,
    priority_ids: Sequence[str],
) -> list[str]:
    priority = set(priority_ids)
    head = [item for item in priority_ids if item in live_dispatch_ids]
    tail = [item for item in live_dispatch_ids if item not in priority]
    return head + tail


def backfill_missing_terminals(
    apply: Callable[[EventRecord], None],
    live_dispatch_ids: Sequence[str],
    *,
    minutes: int = _DEFAULT_LOOKBACK_MINUTES,
    max_ids: int = _DEFAULT_MAX_IDS,
    priority_ids: Sequence[str] | None = None,
    query_terminals: Callable[..., list[dict]] | None = None,
) -> int:
    """Synthesize missing worker terminals from ES for LIVE dispatch ids.

    Returns count of terminal events applied. Idempotent when the fold already
    has ``terminal_ms`` — callers should pass LIVE ids only.
    """
    query_fn = query_terminals or worker_terminals_for_dispatch
    if not live_dispatch_ids:
        return 0
    ordered = _ordered_backfill_ids(
        live_dispatch_ids,
        priority_ids=priority_ids or (),
    )
    applied = 0
    for dispatch_id in ordered[:max_ids]:
        rows = query_fn(dispatch_id, minutes=minutes)
        if not rows:
            continue
        events = events_from_es_worker_terminals(rows, dispatch_id=dispatch_id)
        if not events:
            continue
        earliest = min(events, key=lambda item: item.ts_unix_ms)
        apply(earliest)
        applied += 1
    return applied


def backfill_sdk_fold(
    apply: Callable[[EventRecord], None],
    sdk: SdkFold,
    *,
    minutes: int = _DEFAULT_LOOKBACK_MINUTES,
    max_ids: int = _DEFAULT_MAX_IDS,
    query_terminals: Callable[..., list[dict]] | None = None,
) -> int:
    """Backfill all LIVE SDK rows; prioritize lease-without-terminal zombies."""
    live_ids = live_dispatch_ids(sdk)
    return backfill_missing_terminals(
        apply,
        live_ids,
        minutes=minutes,
        max_ids=max_ids,
        priority_ids=lease_released_without_terminal_ids(sdk),
        query_terminals=query_terminals,
    )


__all__ = [
    "backfill_missing_terminals",
    "backfill_sdk_fold",
    "live_dispatch_ids",
    "lease_released_without_terminal_ids",
    "signal_events",
]
