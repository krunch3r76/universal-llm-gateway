"""Signal-name registry -- the single place G5 reconciles names against live code.

Every signal string the core reacts to is declared here and nowhere else. Fold
logic keys off these constants, so a name that turns out to be wrong at graft
time is a one-line edit in this module rather than a hunt through handlers.

Provenance is tracked per family because it differs sharply:

``manage.charter.tick.*`` -- **VERIFIED**. Named in
``cortex://notes/system/specs/charter-runner-state-close-on-no-gated-pickup.md``
(``root_skipped`` / ``root_closed`` / ``scanned.skipped_by_reason``),
``charter-runner-tick.md`` (``waiting_open``), ``charter-runner-admit-intent-orphan-self-heal.md``
(``intent_healed``), and ``recon/charter-admission-environment-contract/tier1-anchors.md``
(``error``, ``audit``, ``root_skipped`` live samples).

``frontier.sdk.worker.*`` / ``pipeline.frontier.dispatch.*`` -- **PARTLY VERIFIED**.
``frontier.sdk.worker.completed`` and ``pipeline.frontier.dispatch.completed`` /
``.started`` are named as existing signals in ``specs/dispatch-token-cost-rollup.md``,
``specs/model-capability-pricing.md``, ``specs/cached-token-telemetry-prefix-audit.md``
and ``specs/dispatch-surface-unify.md``. The ``.started`` / ``.progress`` / ``.failed``
members of the *worker* family are INFERRED and flagged in ``README.md``.

``cdp.generate.*`` -- **VERIFIED** (G5.2 slice 1). Live emitters in
``frontier_consult/cdp_events.py``; handler table keyed on ``request_id`` per v3 §6.

Naming tension worth a G5 decision, not a core change: ``[universal:events]``
fixes the signal grammar at ``^[a-z]+(\\.[a-z]+){1,4}$`` -- no underscores. Four
live charter signals (``root_skipped``, ``root_closed``, ``waiting_open``,
``intent_healed``) violate it. The core is a consumer and accepts what the bus
actually emits; the divergence belongs to the charter-runner owner.
"""

from __future__ import annotations

# --- charter family (VERIFIED) ---------------------------------------------
CHARTER_SCANNED = "manage.charter.tick.scanned"
CHARTER_ADMITTED = "manage.charter.tick.admitted"
CHARTER_CLOSED = "manage.charter.tick.closed"
CHARTER_ROOT_SKIPPED = "manage.charter.tick.root_skipped"
CHARTER_ROOT_CLOSED = "manage.charter.tick.root_closed"
CHARTER_WAITING_OPEN = "manage.charter.tick.waiting_open"
CHARTER_ERROR = "manage.charter.tick.error"
CHARTER_INTENT_HEALED = "manage.charter.tick.intent_healed"
CHARTER_AUDIT = "manage.charter.tick.audit"
CHARTER_STARTED = "manage.charter.tick.started"
CHARTER_STOPPED = "manage.charter.tick.stopped"
CHARTER_RELOADED = "manage.charter.tick.reloaded"
CHARTER_WINDOW_FAILED = "manage.charter.tick.window_failed"
CHARTER_PAUSED = "manage.charter.tick.paused"
CHARTER_HELD = "manage.charter.tick.held"
CHARTER_RESUMED = "manage.charter.tick.resumed"
CHARTER_FRICTIONS_AUDIT_PASSED = "manage.charter.tick.frictions_audit_passed"
#: Kernel telemetry facade (``charter_runner/telemetry.py``) — informational.
CHARTER_TRANSITION = "manage.charter.tick.transition"
CHARTER_SHADOW_DIFF = "manage.charter.tick.shadow.diff"
CHARTER_SHADOW_STARVED = "manage.charter.tick.shadow.starved"
CHARTER_CONSULT_QUEUED = "manage.charter.tick.consult.queued"
CHARTER_CONSULT_DEFERRED = "manage.charter.tick.consult.deferred"
CHARTER_ENROLLMENT_FILTERED = "manage.charter.tick.enrollment.filtered"

# --- charter conveyor (friction belt) --------------------------------------
CHARTER_CONVEYOR_ENROLLED = "manage.charter.conveyor.enrolled"
CHARTER_CONVEYOR_STALE = "manage.charter.conveyor.stale"
CHARTER_CONVEYOR_DISENROLLED = "manage.charter.conveyor.disenrolled"
CHARTER_CONVEYOR_ENROLL_FAILED = "manage.charter.conveyor.enroll_failed"

#: Signals accepted only as cold-start seed material, never as live transitions.
CHARTER_COLD_START = (CHARTER_AUDIT,)

# --- sdk family: worker lane (GS2 emitter A) -------------------------------
SDK_WORKER_PROGRESS = "frontier.sdk.worker.progress"
SDK_WORKER_TOOLCALL = "frontier.sdk.worker.toolcall"
SDK_WORKER_COMPLETED = "frontier.sdk.worker.completed"
SDK_WORKER_FAILED = "frontier.sdk.worker.failed"
SDK_WORKER_QUEUED = "frontier.sdk.worker.queued"
SDK_WORKER_TIMEOUT = "frontier.sdk.worker.timeout"
SDK_WORKER_ORPHANED = "frontier.sdk.worker.orphaned"
SDK_WORKER_CANCELLED = "frontier.sdk.worker.cancelled"
SDK_WORKER_DELIVERY_FAILED = "frontier.sdk.worker.delivery_failed"
SDK_GENERATE_REQUESTED = "frontier.sdk.generate.requested"
SDK_LEASE_PROMOTED = "frontier.sdk.worker.lease.promoted"
SDK_LEASE_RELEASED = "frontier.sdk.worker.lease.released"
SDK_LEASE_PARK_ENTER = "frontier.sdk.worker.lease.park_enter"
SDK_LEASE_PARK_RESTORE = "frontier.sdk.worker.lease.park_restore"
SDK_CLOSEOUT_RELOCATED = "frontier.sdk.closeout.relocated"
SDK_CLOSEOUT_RECONCILED = "frontier.sdk.closeout.reconciled"
SDK_WORKER_DISPATCHED = "frontier.sdk.worker.dispatched"
SDK_REVIEW_CHILD_SPAWNED = "frontier.sdk.review_child.spawned"

# --- sdk family: pipeline lane (GS2 emitter B) -----------------------------
SDK_PIPELINE_STARTED = "pipeline.frontier.dispatch.started"
SDK_PIPELINE_COMPLETED = "pipeline.frontier.dispatch.completed"
SDK_PIPELINE_FAILED = "pipeline.frontier.dispatch.failed"

#: Emitter identity per signal. Both lanes describe the *same* dispatch; the
#: fold reconciles them rather than letting the later one overwrite the earlier.
EMITTER_WORKER = "worker"
EMITTER_PIPELINE = "pipeline"

SDK_EMITTER_BY_SIGNAL = {
    SDK_WORKER_PROGRESS: EMITTER_WORKER,
    SDK_WORKER_TOOLCALL: EMITTER_WORKER,
    SDK_WORKER_COMPLETED: EMITTER_WORKER,
    SDK_WORKER_FAILED: EMITTER_WORKER,
    SDK_PIPELINE_STARTED: EMITTER_PIPELINE,
    SDK_PIPELINE_COMPLETED: EMITTER_PIPELINE,
    SDK_PIPELINE_FAILED: EMITTER_PIPELINE,
}

SDK_TERMINAL_SIGNALS = frozenset(
    {
        SDK_WORKER_COMPLETED,
        SDK_WORKER_FAILED,
        SDK_PIPELINE_COMPLETED,
        SDK_PIPELINE_FAILED,
    }
)

SDK_FAILURE_SIGNALS = frozenset(
    {
        SDK_WORKER_FAILED,
        SDK_PIPELINE_FAILED,
        SDK_WORKER_TIMEOUT,
        SDK_WORKER_ORPHANED,
        SDK_WORKER_CANCELLED,
    }
)

SDK_LIFECYCLE_SIGNALS = (
    SDK_WORKER_QUEUED,
    SDK_WORKER_TIMEOUT,
    SDK_WORKER_ORPHANED,
    SDK_WORKER_CANCELLED,
    SDK_WORKER_DELIVERY_FAILED,
    SDK_GENERATE_REQUESTED,
    SDK_LEASE_PROMOTED,
    SDK_LEASE_RELEASED,
    SDK_LEASE_PARK_ENTER,
    SDK_LEASE_PARK_RESTORE,
    SDK_CLOSEOUT_RELOCATED,
    SDK_CLOSEOUT_RECONCILED,
    SDK_WORKER_DISPATCHED,
    SDK_REVIEW_CHILD_SPAWNED,
)

# --- cdp family (VERIFIED live — v3 §6) ------------------------------------
CDP_ADMITTED = "cdp.generate.admitted"
CDP_SUBMITTED = "cdp.generate.submitted"
CDP_PROOF = "cdp.generate.proof"
CDP_STALLED = "cdp.generate.stalled"
CDP_DELIVERY_FAILED = "cdp.generate.delivery_failed"

#: Earliest G3 marker; filtered to ``reply_from_agent == \"cdp\"`` in the fold.
POLL_HINT_ISSUED = "frontier.poll.hint.issued"

CDP_TERMINAL_SIGNALS = frozenset({CDP_PROOF, CDP_STALLED, CDP_DELIVERY_FAILED})

#: G4 contract signals never emitted live — removed from handler table G5.2 slice 1.
CDP_PHANTOM = (
    "cdp.generate.running",
    "cdp.generate.progress",
    "cdp.generate.completed",
    "cdp.generate.failed",
    "cdp.generate.aborted",
)

# --- realtime-plane drop counters (VERIFIED, doctrine §2) ------------------
EVENTS_DROPPED_INGEST = "events.dropped.ingest"
EVENTS_DROPPED_SUBSCRIBE = "events.dropped.subscribe"

# --- cold-start seed meta (G5 graft — not emitted on the live bus) ---------
MONITOR_SEED_FOLD_STATUS = "monitor.seed.fold_status"
#: GX1 replay window could not be satisfied on reconnect (graft-only meta).
MONITOR_TRANSPORT_REPLAY_TRUNCATED = "monitor.transport.replay_truncated"
#: Synthetic SDK row injection from reconcile / lease-snapshot (D2: no live emitter).
MONITOR_META_SDK_STARTED = "monitor.meta.sdk_started"
MONITOR_META_CHARTER_OBJECTIVE = "monitor.meta.charter_objective"
#: Reconcile source failure — graft-only; drives attention, never steady-state poll.
MONITOR_RECONCILE_SOURCE_FAILED = "monitor.reconcile.source_failed"
#: Stargate session boundary — used to terminalize restart-survivor review children.
SYSTEM_STARTED = "system.started"

#: Payload key graft uses to mark lease-snapshot reconcile rows. SdkFold reads
#: this; live bus signals omit it and therefore count as ``signal`` provenance.
PROVENANCE_RECONCILED_KEY = "_provenance"
PROVENANCE_RECONCILED = "reconciled"

CHARTER_FAMILY = (
    CHARTER_SCANNED,
    CHARTER_ADMITTED,
    CHARTER_CLOSED,
    CHARTER_ROOT_SKIPPED,
    CHARTER_ROOT_CLOSED,
    CHARTER_WAITING_OPEN,
    CHARTER_ERROR,
    CHARTER_INTENT_HEALED,
    CHARTER_AUDIT,
    CHARTER_STARTED,
    CHARTER_STOPPED,
    CHARTER_RELOADED,
    CHARTER_WINDOW_FAILED,
    CHARTER_PAUSED,
    CHARTER_HELD,
    CHARTER_RESUMED,
    CHARTER_FRICTIONS_AUDIT_PASSED,
    CHARTER_TRANSITION,
    CHARTER_SHADOW_DIFF,
    CHARTER_SHADOW_STARVED,
    CHARTER_CONSULT_QUEUED,
    CHARTER_CONSULT_DEFERRED,
    CHARTER_ENROLLMENT_FILTERED,
    CHARTER_CONVEYOR_ENROLLED,
    CHARTER_CONVEYOR_STALE,
    CHARTER_CONVEYOR_DISENROLLED,
    CHARTER_CONVEYOR_ENROLL_FAILED,
)

SDK_FAMILY = tuple(SDK_EMITTER_BY_SIGNAL) + SDK_LIFECYCLE_SIGNALS

CDP_FAMILY = (
    CDP_ADMITTED,
    CDP_SUBMITTED,
    CDP_PROOF,
    CDP_STALLED,
    CDP_DELIVERY_FAILED,
    POLL_HINT_ISSUED,
)

META_FAMILY = (
    EVENTS_DROPPED_INGEST,
    EVENTS_DROPPED_SUBSCRIBE,
    MONITOR_SEED_FOLD_STATUS,
    MONITOR_TRANSPORT_REPLAY_TRUNCATED,
    MONITOR_META_SDK_STARTED,
    MONITOR_META_CHARTER_OBJECTIVE,
    MONITOR_RECONCILE_SOURCE_FAILED,
    SYSTEM_STARTED,
)

#: Every signal the handler table claims. ``README.md`` restates this list; the
#: coverage test asserts the two agree, so the README cannot drift silently.
ALL_HANDLED = CHARTER_FAMILY + SDK_FAMILY + CDP_FAMILY + META_FAMILY
