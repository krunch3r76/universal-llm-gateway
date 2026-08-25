"""Unified attention derivation -- every threshold in the system lives here.

One flat ``attention[]``, not per-panel warnings, because the operator's question
is "what needs me?" and answering it should not require reading four panels and
mentally merging them.

Two properties the View depends on:

* **Stable keys.** ``AttentionItem.key`` is a pure function of the condition and
  its subject, so the same condition yields the same key on every tick. A View can
  diff, dedupe or hold a dismissal without deriving anything.
* **Total order.** Items sort by severity (strongest first), then kind, then
  subject. No dict iteration order reaches the output, so two replays of one
  fixture produce byte-identical attention -- which is what makes the fingerprint
  determinism test meaningful.

Every staleness rule is an **idle** rule: elapsed time since the last observed
progress signal, never a wall-clock completion budget
(``[universal:obs-over-timeouts]``). A dispatch that keeps emitting progress never
trips one of these, however long it runs.
"""

from __future__ import annotations

from collections.abc import Mapping

from .attention_plane import (
    duplicate_refused_items,
    plane_items,
    reconcile_failure_items,
)
from .attention_sdk import sdk_items
from .attention_util import escalate, fill_ages, secs
from .dtos import (
    AttentionItem,
    CdpLegRow,
    CharterRootRow,
    HealthProjection,
    SdkDispatchRow,
    Thresholds,
    severity_rank,
)


def _charter_items(
    health: HealthProjection, roots: tuple[CharterRootRow, ...], thresholds: Thresholds
) -> list[AttentionItem]:
    """Derive attention for tick health and per-root posture."""
    items: list[AttentionItem] = []
    if health.tick_last_scan_ms is None:
        items.append(
            AttentionItem(
                key="charter.tick.never-scanned",
                kind="charter.tick.never_scanned",
                severity="info",
                subject="charter-runner",
                title="No charter scan observed yet",
                detail="Cold start, or the tick is not emitting. Not yet a fault.",
            )
        )
    else:
        severity = escalate(
            health.tick_last_scan_age_ms,
            thresholds.tick_stale_warn_ms,
            thresholds.tick_stale_crit_ms,
        )
        if severity:
            items.append(
                AttentionItem(
                    key="charter.tick.stale",
                    kind="charter.tick.stale",
                    severity=severity,
                    subject="charter-runner",
                    title="Charter tick has not scanned recently",
                    detail=f"Last scan {secs(health.tick_last_scan_age_ms)} ago.",
                    since_ms=health.tick_last_scan_ms,
                    age_ms=health.tick_last_scan_age_ms,
                )
            )
    # Only surface when the error is still current: a later successful scan
    # (or fold clear-on-scan) means the loop recovered — do not latch forever.
    if health.tick_last_error_ms is not None and (
        health.tick_last_scan_ms is None
        or health.tick_last_error_ms >= health.tick_last_scan_ms
    ):
        items.append(
            AttentionItem(
                key="charter.tick.error",
                kind="charter.tick.error",
                severity="crit",
                subject="charter-runner",
                title="Charter tick reported an error",
                detail=health.tick_last_error_message or "unspecified",
                since_ms=health.tick_last_error_ms,
            )
        )
    for root in roots:
        items.extend(_root_items(root, thresholds))
    return items


def _root_items(root: CharterRootRow, thresholds: Thresholds) -> list[AttentionItem]:
    """Derive attention for one charter root."""
    items: list[AttentionItem] = []
    if root.state == "failed":
        items.append(
            AttentionItem(
                key=f"charter.root.window-failed:{root.root_id}",
                kind="charter.root.window_failed",
                severity="crit",
                subject=root.root_id,
                title="Charter window failed",
                detail=root.skip_reason or "unspecified",
                since_ms=root.last_signal_ms,
            )
        )
    if root.state == "waiting_open" and root.waiting_open_since_ms is not None:
        items.append(
            AttentionItem(
                key=f"charter.root.waiting-open:{root.root_id}",
                kind="charter.root.waiting_open",
                severity="warn",
                subject=root.root_id,
                title="Root waiting for its IDE window",
                detail="Soft remind. No auto-fail unless the stale guard is armed.",
                since_ms=root.waiting_open_since_ms,
            )
        )
    if root.state == "parked":
        items.append(
            AttentionItem(
                key=f"charter.root.parked:{root.root_id}",
                kind="charter.root.parked",
                severity="warn",
                subject=root.root_id,
                title="Parked parent: worker leg terminal, root still open",
                detail=(
                    f"Worker thread {root.worker_thread or 'unknown'} reached a "
                    "terminal state but no root_closed was observed. Harvest may "
                    "be pending, or the CHECKPOINT never landed."
                ),
                since_ms=root.last_signal_ms,
                age_ms=root.in_flight_age_ms,
            )
        )
    if root.skip_streak >= thresholds.skip_streak_warn and not root.closed:
        items.append(
            AttentionItem(
                key=f"charter.root.skip-streak:{root.root_id}",
                kind="charter.root.skip_streak",
                severity="warn",
                subject=root.root_id,
                title=f"Root skipped {root.skip_streak} consecutive ticks",
                detail=f"Reason: {root.skip_reason or 'unspecified'}. Needs disposition.",
                since_ms=root.last_signal_ms,
            )
        )
    if root.state == "stuck":
        items.append(
            AttentionItem(
                key=f"charter.root.stuck:{root.root_id}",
                kind="charter.root.stuck",
                severity="crit",
                subject=root.root_id,
                title="Charter root wedged — consult queue or refire refusal",
                detail=root.skip_reason or "consult_queued_streak or identical_work_refire",
                since_ms=root.last_signal_ms,
            )
        )
    return items


def _cdp_items(
    legs: tuple[CdpLegRow, ...], thresholds: Thresholds
) -> list[AttentionItem]:
    """Derive attention for CDP legs per v3 §9 arc-specific classes."""
    items: list[AttentionItem] = []
    for row in legs:
        subject = row.request_id
        if row.state == "stalled":
            items.append(
                AttentionItem(
                    key=f"cdp.leg.stalled:{subject}",
                    kind="cdp.leg.stalled",
                    severity="crit",
                    subject=subject,
                    title="CDP leg stalled without proof",
                    detail=row.failure_reason or row.stall_stage or "unspecified",
                    since_ms=row.terminal_ms,
                )
            )
        if row.state == "delivery_failed":
            items.append(
                AttentionItem(
                    key=f"cdp.leg.delivery_failed:{subject}",
                    kind="cdp.leg.delivery_failed",
                    severity="crit",
                    subject=subject,
                    title="CDP delivery failed — outcome not on the bus",
                    detail=row.failure_reason or "on-behalf bus delivery exhausted",
                    since_ms=row.terminal_ms,
                )
            )
        if row.terminal_ms is None and row.admitted_at_ms is not None:
            wall_ms = row.max_wall_s * 1000
            warn_ms = (wall_ms * 2) // 3
            if row.elapsed_ms is not None and row.elapsed_ms >= warn_ms:
                items.append(
                    AttentionItem(
                        key=f"cdp.leg.wall_approaching:{subject}",
                        kind="cdp.leg.wall_approaching",
                        severity="warn",
                        subject=subject,
                        title="CDP leg approaching wall ceiling",
                        detail=(
                            f"Elapsed {secs(row.elapsed_ms)} of {row.max_wall_s}s max wall. "
                            "Silence is not failure before the ceiling."
                        ),
                        since_ms=row.admitted_at_ms,
                        age_ms=row.elapsed_ms,
                    )
                )
    return items


def derive_attention(
    *,
    health: HealthProjection,
    roots: tuple[CharterRootRow, ...],
    dispatches: tuple[SdkDispatchRow, ...],
    legs: tuple[CdpLegRow, ...],
    thresholds: Thresholds,
    now_ms: int,
    reconcile_failures: Mapping[str, tuple[str, str, str] | tuple[str, str, str, int]]
    | None = None,
    ingest_since_ms: int | None = None,
    subscribe_since_ms: int | None = None,
    unhandled_since_ms: int | None = None,
    duplicate_refused: Mapping[str, tuple[int, str, str]] | None = None,
) -> tuple[AttentionItem, ...]:
    """Return the unified, totally-ordered attention list for one frame."""
    items = _charter_items(health, roots, thresholds)
    items += sdk_items(dispatches, thresholds)
    items += _cdp_items(legs, thresholds) + plane_items(
        health,
        ingest_since_ms=ingest_since_ms,
        subscribe_since_ms=subscribe_since_ms,
        unhandled_since_ms=unhandled_since_ms,
    )
    if reconcile_failures:
        items += reconcile_failure_items(reconcile_failures)
    if duplicate_refused:
        items += duplicate_refused_items(duplicate_refused)
    items = fill_ages(items, now_ms)
    items.sort(key=lambda i: (-severity_rank(i.severity), i.kind, i.subject, i.key))
    return tuple(items)
