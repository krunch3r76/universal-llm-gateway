"""Frozen projection DTOs -- the entire View-facing vocabulary.

Every row is an immutable dataclass. The View renders these and derives nothing:
no folds, no thresholds, no CHECKPOINT parsing, no bus queries.

Field-level provenance caveat: the v3 design authority
(``workspaces://universal-llm-gateway/tmp/prompts/charter-tick-monitor-design-refined-v3.md``)
is unreadable from a CDP seat, and Fable G3 recorded the same gap as G-a. DTO
*names* are v3's, taken from the MC playbook §2/§8 and Fable §3.3. Field *sets*
are this pass's proposal, chosen to be derivable from signals whose payloads are
attested in Cortex. G5 reconciles field-by-field against v3 at ratification.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

#: Bumped only on a breaking change. Additive optional fields stay at 1.
#: A View whose compiled major exceeds this refuses to render rather than
#: rendering a newer schema partially (Fable §3.3).
SCHEMA_VERSION = 1

# Attention severities, ordered weakest to strongest. Ordering is load-bearing:
# ``attention[]`` sorts by it, so the tuple's index is the rank.
SEVERITIES = ("info", "warn", "crit")


@dataclass(frozen=True)
class Thresholds:
    """Attention thresholds. The Model owns these; the View never sees a number.

    All windows are **idle** windows -- elapsed time since the last observed
    progress signal -- never wall-clock completion budgets
    (``[universal:obs-over-timeouts]``). A dispatch that keeps emitting progress
    never trips one of these no matter how long it runs.
    """

    tick_stale_warn_ms: int = 180_000
    tick_stale_crit_ms: int = 600_000
    sdk_idle_warn_ms: int = 300_000
    sdk_idle_crit_ms: int = 900_000
    cdp_idle_warn_ms: int = 300_000
    cdp_idle_crit_ms: int = 600_000
    waiting_open_warn_ms: int = 300_000
    parked_parent_warn_ms: int = 120_000
    skip_streak_warn: int = 5


@dataclass(frozen=True)
class HealthProjection:
    """Global health, lease/queue posture, and the monitor's own honesty counters.

    ``unhandled_signals`` and ``degraded`` are self-diagnosis: a signal the fold
    does not recognise, or a family that has gone silent, is surfaced rather than
    absorbed. Schema drift between the emitters and this core shows up here first.
    """

    tick_last_scan_ms: int | None = None
    tick_last_scan_age_ms: int | None = None
    tick_roots_scanned: int = 0
    tick_admitted_last_scan: int = 0
    tick_admitted_total: int = 0
    tick_last_error_ms: int | None = None
    tick_last_error_message: str | None = None
    skipped_by_reason: Mapping[str, int] = field(default_factory=dict)
    lease_holder: str | None = None
    lease_expires_ms: int | None = None
    queue_depth: int = 0
    wip_capacity: int | None = None
    wip_in_use: int = 0
    events_dropped_ingest: int = 0
    events_dropped_subscribe: int = 0
    records_folded: int = 0
    unhandled_signals: Mapping[str, int] = field(default_factory=dict)
    seq_high_water: int | None = None
    cold_start_seeded: bool = False
    fold_status: str = "live"
    charter_loop_state: str = "unknown"
    charter_last_reload_ms: int | None = None
    charter_reload_module_count: int = 0
    charter_hold: bool | None = None
    charter_hold_reason: str | None = None
    degraded: tuple[str, ...] = ()


@dataclass(frozen=True)
class CharterRootRow:
    """One enrolled charter root.

    ``arc_g_step`` / ``arc_g_step_label`` are **mirror fields**: they carry
    ``path_sim_g_step`` straight off the admission payload when G5 lands it, and
    stay ``None`` otherwise. The core never parses a CHECKPOINT to fill them.

    ``pickup_gid`` is the standing ledger tip (next gated row), grafted from the
    root ledger at cold-start. Paint prefers ``arc_g_step`` when present, else
    ``pickup_gid``.
    """

    root_id: str
    state: str = "unknown"
    project: str | None = None
    worker_thread: str | None = None
    window_index: int | None = None
    admission_mode: str | None = None
    packet_path: str | None = None
    arc_g_step: str | None = None
    arc_g_step_label: str | None = None
    pickup_gid: str | None = None
    objective: str | None = None
    bus_slug: str | None = None
    bus_summary: str | None = None
    last_signal_ms: int | None = None
    last_signal: str | None = None
    admitted_at_ms: int | None = None
    in_flight_age_ms: int | None = None
    skip_reason: str | None = None
    skip_streak: int = 0
    checkpoint_turn: int | None = None
    waiting_open_since_ms: int | None = None
    closed: bool = False
    unenrolled: bool = False


@dataclass(frozen=True)
class SdkDispatchRow:
    """One cursor-sdk dispatch, reconciled across both GS2 emitters.

    ``emitters_seen`` records which lanes spoke; ``divergent_fields`` names any
    field on which their terminals disagreed. Divergence is reported, never
    silently reconciled -- a monitor that picks a winner hides the defect it
    exists to surface.
    """

    dispatch_id: str
    state: str = "unknown"
    root_id: str | None = None
    thread_id: str | None = None
    seat: str | None = None
    role: str | None = None
    model: str | None = None
    contract: str | None = None
    started_ms: int | None = None
    last_progress_ms: int | None = None
    terminal_ms: int | None = None
    duration_ms: int | None = None
    #: Live wall age since ``started_ms`` (None when terminal — use ``duration_ms``).
    elapsed_ms: int | None = None
    #: Live age since last progress heartbeat (stall signal; ≠ wall elapsed).
    idle_age_ms: int | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    cached_tokens: int | None = None
    stall_stage: str | None = None
    failure_reason: str | None = None
    emitters_seen: tuple[str, ...] = ()
    divergent_fields: tuple[str, ...] = ()
    terminal_emitter: str | None = None
    provenance: str = "signal"
    queue_position: int | None = None
    closeout_uri: str | None = None
    delivery_failed: bool = False
    implement_gate_bypass: bool = False
    lease_released_without_terminal: bool = False
    last_tool_name: str | None = None
    last_tool_status: str | None = None
    tool_call_count: int | None = None
    parent_execution_id: str | None = None
    review_child: bool = False
    admitted_via: str | None = None
    asked_by: str | None = None
    purpose: str | None = None
    story_id: str | None = None
    topic: str | None = None
    nest_under: str | None = None
    resume_of: str | None = None


@dataclass(frozen=True)
class CdpLegRow:
    """One CDP generate leg (v3 §2.2 / §6).

    Keyed on ``request_id`` — the only field present on every ``cdp.generate.*``
    payload. G3 is a black box between ``admitted`` and terminal; silence before
    the wall ceiling is not failure (§6.2).
    """

    request_id: str
    execution_id: str | None = None
    satellite_execution_id: str | None = None
    thread_id: str | None = None
    model: str | None = None
    caller_agent: str | None = None
    topic: str | None = None
    chat_url: str | None = None
    state: str = "unknown"
    admitted_at_ms: int | None = None
    terminal_ms: int | None = None
    elapsed_ms: int | None = None
    max_wall_s: int = 1800
    archive_uri: str | None = None
    content_proof_uri: str | None = None
    stall_stage: str | None = None
    failure_reason: str | None = None
    proof_present: bool = False
    root_id: str | None = None
    provenance: str = "signal"


@dataclass(frozen=True)
class PathSimArcRow:
    """A path-sim arc. Declared for v1.1; ``arcs`` is present-but-empty in v1.

    Populated only once GP1 ships ``checkpoint_folded`` as an event. Until then
    filling this would require parsing CHECKPOINT prose, which the core forbids.
    """

    arc_id: str
    root_id: str | None = None
    g_step: str | None = None
    g_step_label: str | None = None
    project: str | None = None
    checkpoint_uri: str | None = None
    updated_ms: int | None = None


@dataclass(frozen=True)
class AttentionItem:
    """One thing asking for the operator's eyes.

    ``key`` is stable across ticks for the same underlying condition, so a View
    can diff, dedupe, or hold a dismissal without re-deriving anything.
    """

    key: str
    kind: str
    severity: str
    subject: str
    title: str
    detail: str = ""
    since_ms: int | None = None
    age_ms: int | None = None
    target_uri: str | None = None


@dataclass(frozen=True)
class RelationEdge:
    """One evidence-backed relationship. Views may paint; they must not invent."""

    kind: str
    from_id: str
    to_id: str
    evidence_signal: str


@dataclass(frozen=True)
class SupervisorProjection:
    """One immutable frame. The whole of what a View is allowed to know.

    ``fingerprint`` covers the *state* of the frame, deliberately excluding
    ``generated_at_ms``, ``changed_hints`` and every ``*_age_ms`` field, so a
    quiescent system at 30 Hz emits nothing. It doubles as the determinism
    falsifier: replay a fixture twice at the same ``now_ms`` and the hash must
    match.

    ``changed_hints`` is **advisory only**, relative to the previous frame
    *delivered to that subscriber*. Under drop-oldest broadcast a subscriber that
    lost frames cannot trust them, so the Controller must stamp ``("*",)`` on the
    first delivery after any drop. A View that treats hints as authoritative goes
    stale silently, and only under load.
    """

    schema_version: int = SCHEMA_VERSION
    generated_at_ms: int = 0
    fingerprint: str = ""
    health: HealthProjection = field(default_factory=HealthProjection)
    roots: tuple[CharterRootRow, ...] = ()
    sdk: tuple[SdkDispatchRow, ...] = ()
    cdp: tuple[CdpLegRow, ...] = ()
    attention: tuple[AttentionItem, ...] = ()
    relations: tuple[RelationEdge, ...] = ()
    arcs: Mapping[str, PathSimArcRow] = field(default_factory=dict)
    changed_hints: tuple[str, ...] = ()


def severity_rank(severity: str) -> int:
    """Return the sort rank of ``severity``; unknown values sort weakest."""
    try:
        return SEVERITIES.index(severity)
    except ValueError:
        return -1
