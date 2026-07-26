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

from typing import Mapping

from .dtos import (
    AttentionItem,
    CdpLegRow,
    CharterRootRow,
    HealthProjection,
    SdkDispatchRow,
    Thresholds,
    severity_rank,
)


def _escalate(age_ms: int | None, warn_ms: int, crit_ms: int) -> str | None:
    """Return the severity ``age_ms`` earns, or ``None`` if under the warn floor."""
    if age_ms is None:
        return None
    if age_ms >= crit_ms:
        return "crit"
    if age_ms >= warn_ms:
        return "warn"
    return None


def _secs(age_ms: int | None) -> str:
    """Render an age in whole seconds for operator-facing detail text."""
    return "unknown" if age_ms is None else f"{age_ms // 1000}s"


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
        severity = _escalate(
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
                    detail=f"Last scan {_secs(health.tick_last_scan_age_ms)} ago.",
                    since_ms=health.tick_last_scan_ms,
                    age_ms=health.tick_last_scan_age_ms,
                )
            )
    if health.tick_last_error_ms is not None:
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
    return items


def _sdk_items(
    dispatches: tuple[SdkDispatchRow, ...], thresholds: Thresholds
) -> list[AttentionItem]:
    """Derive attention for cursor-sdk dispatches, including GS2 divergence."""
    items: list[AttentionItem] = []
    for row in dispatches:
        if row.divergent_fields:
            items.append(
                AttentionItem(
                    key=f"sdk.emitter.divergence:{row.dispatch_id}",
                    kind="sdk.emitter.divergence",
                    severity="crit",
                    subject=row.dispatch_id,
                    title="GS2: emitters disagree about this dispatch",
                    detail=(
                        "Divergent fields: "
                        + ", ".join(row.divergent_fields)
                        + f". Emitters: {', '.join(row.emitters_seen)}; first terminal "
                        f"from {row.terminal_emitter or 'unknown'} was kept. "
                        "Not reconciled by design."
                    ),
                    since_ms=row.terminal_ms,
                )
            )
        if row.state == "failed":
            items.append(
                AttentionItem(
                    key=f"sdk.dispatch.failed:{row.dispatch_id}",
                    kind="sdk.dispatch.failed",
                    severity="crit",
                    subject=row.dispatch_id,
                    title="Dispatch failed",
                    detail=row.failure_reason or "no reason reported",
                    since_ms=row.terminal_ms,
                )
            )
        if row.terminal_ms is None:
            severity = _escalate(
                row.idle_age_ms,
                thresholds.sdk_idle_warn_ms,
                thresholds.sdk_idle_crit_ms,
            )
            if severity:
                items.append(
                    AttentionItem(
                        key=f"sdk.dispatch.idle:{row.dispatch_id}",
                        kind="sdk.dispatch.idle",
                        severity=severity,
                        subject=row.dispatch_id,
                        title="Dispatch has emitted no progress recently",
                        detail=(
                            f"Idle {_secs(row.idle_age_ms)}"
                            + (f" at stage {row.stall_stage}" if row.stall_stage else "")
                            + ". Idle window, not a completion deadline."
                        ),
                        since_ms=row.last_progress_ms,
                        age_ms=row.idle_age_ms,
                    )
                )
    return items


def _cdp_items(
    legs: tuple[CdpLegRow, ...], thresholds: Thresholds
) -> list[AttentionItem]:
    """Derive attention for CDP legs, including the proofless-completion case."""
    items: list[AttentionItem] = []
    warn, crit = thresholds.cdp_idle_warn_ms, thresholds.cdp_idle_crit_ms
    for row in legs:
        if row.state == "completed" and not row.proof_present:
            items.append(
                AttentionItem(
                    key=f"cdp.leg.proofless:{row.execution_id}",
                    kind="cdp.leg.completed_without_proof",
                    severity="warn",
                    subject=row.execution_id,
                    title="CDP leg completed with no archive or content proof",
                    detail=(
                        "Terminal carried neither archive_uri nor content_proof_uri. "
                        "Treat as unharvested, not as success."
                    ),
                    since_ms=row.terminal_ms,
                )
            )
        if row.state in ("failed", "aborted"):
            items.append(
                AttentionItem(
                    key=f"cdp.leg.terminal:{row.execution_id}",
                    kind=f"cdp.leg.{row.state}",
                    severity="crit" if row.state == "failed" else "warn",
                    subject=row.execution_id,
                    title=f"CDP leg {row.state}",
                    detail=row.failure_reason or "no reason reported",
                    since_ms=row.terminal_ms,
                )
            )
        if row.terminal_ms is None:
            severity = _escalate(row.idle_age_ms, warn, crit)
            if severity:
                items.append(
                    AttentionItem(
                        key=f"cdp.leg.idle:{row.execution_id}",
                        kind="cdp.leg.idle",
                        severity=severity,
                        subject=row.execution_id,
                        title="CDP leg quiet",
                        detail=(
                            f"No progress for {_secs(row.idle_age_ms)}. Silence is not "
                            "failure -- the leg may be alive and quiet."
                        ),
                        since_ms=row.last_progress_ms,
                        age_ms=row.idle_age_ms,
                    )
                )
    return items


def _plane_items(health: HealthProjection) -> list[AttentionItem]:
    """Derive attention for realtime-plane drops and schema drift."""
    items: list[AttentionItem] = []
    for count, label, key in (
        (health.events_dropped_ingest, "ingest", "events.dropped.ingest"),
        (health.events_dropped_subscribe, "subscribe", "events.dropped.subscribe"),
    ):
        if count:
            items.append(
                AttentionItem(
                    key=key,
                    kind=key,
                    severity="warn" if label == "subscribe" else "crit",
                    subject="event-plane",
                    title=f"{count} {label} drop event(s) observed",
                    detail=(
                        "Subscribe drops are correct under overload; ingest drops mean "
                        "fold inputs were lost and folded state may be incomplete."
                    ),
                )
            )
    if health.unhandled_signals:
        names = ", ".join(sorted(health.unhandled_signals))
        items.append(
            AttentionItem(
                key="signal.unhandled",
                kind="signal.unhandled",
                severity="warn",
                subject="monitor-core",
                title="Signals arrived that the handler table does not cover",
                detail=(
                    f"Unhandled: {names}. Either the emitters moved or this core's "
                    "signal registry is stale. Reconcile signals.py."
                ),
            )
        )
    return items


def _reconcile_failure_items(
    failures: Mapping[str, tuple[str, str, str]],
) -> list[AttentionItem]:
    """Surface click-time reconcile source failures to the operator."""
    items: list[AttentionItem] = []
    for key, (subject, source, error) in sorted(failures.items()):
        items.append(
            AttentionItem(
                key=key,
                kind="monitor.reconcile.source_failed",
                severity="warn",
                subject=subject,
                title=f"Reconcile {source} failed",
                detail=f"{source}: {error}",
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
    reconcile_failures: Mapping[str, tuple[str, str, str]] | None = None,
) -> tuple[AttentionItem, ...]:
    """Return the unified, totally-ordered attention list for one frame."""
    items = _charter_items(health, roots, thresholds)
    items += _sdk_items(dispatches, thresholds)
    items += _cdp_items(legs, thresholds) + _plane_items(health)
    if reconcile_failures:
        items += _reconcile_failure_items(reconcile_failures)
    items.sort(key=lambda i: (-severity_rank(i.severity), i.kind, i.subject, i.key))
    return tuple(items)
