"""Charter harvest propagation — drain-aware service restart after window close.

When a worker closeout carries structured ``propagation`` rows or legacy
``propagation_residue`` (landed≠live), the charter tick persists open rows,
applies the safe-window matrix plus GIW I2, fires ``sync_restart`` only when
permitted, and closes rows only on observed proof-of-live.
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import httpx
from charter_runner_store.propagation_ledger import (
    OpenPropagationProjection,
    bump_age_for_open_rows,
    close_row,
    fail_row,
    list_open_rows,
    scoreboard_projection,
    set_defer_reason,
    upsert_open_rows,
)
from charter_runner_store.propagation_liveness import observe_code_ref_live
from charter_runner_store.propagation_terminal import settle_open_row
from deploy_identity.code_ref_relation import code_ref_relation
from implement_admission.propagation_admit_validation import (
    CLIENT_VISIBLE_SERVICES,
    MANAGE_SERVICE_SLUGS,
    SERVED_ARTIFACT_SERVICES,
)
from implement_admission.propagation_row import (
    PropagationRow,
    default_proof,
    resolve_code_ref,
    rows_from_closeout_payload,
)
from implement_admission.service_lib_ownership import (
    audit_sync_restart_slug,
    declared_services_for_lib_path,
    path_prefixes,
    slug_for_service_path,
)

from scripts.model_manager.ui.charter_scoreboard_objective import (
    scoreboard_path_for_root,
)
from scripts.model_manager.ui.charter_scoreboard_propagation import (
    write_scoreboard_open_rows,
)

from .bus_client import closeout_turn_from_turns
from .propagation_libs_closure import lib_name_for_path, services_for_lib_path

if TYPE_CHECKING:
    from universal_event_bus import EventBus

    from scripts.model_manager.ui.controller.service_ctl.core import ServiceController

logger = logging.getLogger(__name__)

_CHARTER_RUNNER_PREFIX = "scripts/model_manager/ui/controller/charter_runner/"
_SYNC_RESTART_SLUG_RE = re.compile(
    r"^sync_restart:\s*([a-z][a-z0-9_]*)",
    re.IGNORECASE,
)
_FILE_FIELDS = ("files_created", "files_modified", "files_deleted")

_GIW_LIVENESS_URL = os.environ.get(
    "GIT_INTEGRATION_WORKER_LIVENESS_URL",
    "http://127.0.0.1:8091/api/v1/git/cursor-auto/liveness",
)
_GIW_QUEUE_URL = os.environ.get(
    "GIT_INTEGRATION_WORKER_QUEUE_URL",
    "http://127.0.0.1:8091/api/v1/git/cursor-auto/queue",
)

_context: tuple[ServiceController, EventBus | None] | None = None
_pending_charter_reload: bool = False
_probe_client: httpx.Client | None = None

ProbeCallable = Callable[[PropagationRow], dict[str, Any] | None]


@dataclass(frozen=True)
class ProbeDispatchResult:
    """Outcome of dispatching one row's proof_class to a registered probe."""

    payload: dict[str, Any] | None
    proof_class_requested: str
    proof_class_executed: str | None
    error: str | None


def _probe_process_live_row(row: PropagationRow) -> dict[str, Any] | None:
    payload = probe_process_live(row.service)
    if not isinstance(payload, dict):
        return payload
    version = payload.get("code_version")
    if isinstance(version, str):
        return {
            **payload,
            "proof_class_executed": "process_live",
            "code_ref_relation": code_ref_relation(row.code_ref, version),
        }
    return {**payload, "proof_class_executed": "process_live"}


def _probe_client_visible_row(row: PropagationRow) -> dict[str, Any] | None:
    from deploy_identity.mcp_health_probe_url import resolve_mcp_health_probe_url

    from services.git_integration_worker.cursor_auto.propagation_probe import (
        _fetch_cortex_api_health,
    )

    mcp_health = _fetch_json(resolve_mcp_health_probe_url())
    cortex_health = _fetch_cortex_api_health()
    if mcp_health is None and cortex_health is None:
        return None
    payload: dict[str, Any] = {
        "mcp_health": mcp_health,
        "cortex_api": cortex_health,
        "proof_class_executed": "client_visible",
    }
    for section in (mcp_health, cortex_health):
        if isinstance(section, dict):
            version = section.get("code_version")
            if isinstance(version, str):
                payload["code_ref_relation"] = code_ref_relation(row.code_ref, version)
    return payload


def _probe_served_artifact_row(row: PropagationRow) -> dict[str, Any] | None:
    from services.git_integration_worker.cursor_auto.propagation_served_artifact import (
        probe_served_artifact,
        served_artifact_descriptor,
    )

    descriptor = served_artifact_descriptor(row.service)
    if descriptor is None:
        return None
    expected = row.expected_x_mcp_count or descriptor.expected_x_mcp_count
    payload = probe_served_artifact(
        row.service,
        code_ref=row.code_ref,
        expected_x_mcp_count=expected,
    )
    if isinstance(payload, dict):
        return {**payload, "proof_class_executed": "served_artifact"}
    return payload


def _build_proof_probe_registry() -> dict[tuple[str, str], ProbeCallable]:
    registry: dict[tuple[str, str], ProbeCallable] = {}
    for slug in MANAGE_SERVICE_SLUGS:
        registry[(slug, "process_live")] = _probe_process_live_row
    for slug in SERVED_ARTIFACT_SERVICES:
        registry[(slug, "served_artifact")] = _probe_served_artifact_row
    for slug in CLIENT_VISIBLE_SERVICES:
        registry[(slug, "client_visible")] = _probe_client_visible_row
    return registry


PROOF_PROBE_REGISTRY: dict[tuple[str, str], ProbeCallable] = _build_proof_probe_registry()


def registered_proof_classes(service: str) -> frozenset[str]:
    """Return proof classes with a registered probe for *service*."""
    slug = service.strip().lower()
    return frozenset(
        proof_class
        for (svc, proof_class) in PROOF_PROBE_REGISTRY
        if svc == slug
    )


def proof_class_unsupported_detail(service: str, proof_class: str) -> str:
    """Build the fail-loud token when no probe is registered."""
    slug = service.strip().lower()
    registered = sorted(registered_proof_classes(slug))
    return (
        f"proof_class_unsupported: service={slug} "
        f"requested={proof_class.strip()} "
        f"registered={','.join(registered)}"
    )


def dispatch_proof_probe(row: PropagationRow) -> ProbeDispatchResult:
    """Dispatch *row*'s requested proof_class — no silent default override."""
    requested = row.proof_class_requested or row.proof_class
    probe_fn = PROOF_PROBE_REGISTRY.get((row.service, requested))
    if probe_fn is None:
        return ProbeDispatchResult(
            payload=None,
            proof_class_requested=requested,
            proof_class_executed=None,
            error=proof_class_unsupported_detail(row.service, requested),
        )
    payload = probe_fn(row)
    executed = requested
    if isinstance(payload, dict):
        executed = str(payload.get("proof_class_executed") or requested)
    return ProbeDispatchResult(
        payload=payload,
        proof_class_requested=requested,
        proof_class_executed=executed,
        error=None,
    )


def schedule_charter_reload() -> None:
    """Request in-process charter reload after the current tick slice finishes."""
    global _pending_charter_reload
    _pending_charter_reload = True


def consume_pending_charter_reload() -> bool:
    """Return and clear a deferred charter_reload request."""
    global _pending_charter_reload
    if not _pending_charter_reload:
        return False
    _pending_charter_reload = False
    return True


def install_propagation_context(
    ctl: ServiceController | None,
    *,
    event_bus: EventBus | None = None,
) -> None:
    """Bind manage ServiceController for harvest-time propagation (tick start/stop)."""
    global _context
    _context = (ctl, event_bus) if ctl is not None else None


@dataclass
class PropagationPlan:
    """Resolved propagation actions for one harvested window."""

    rows: list[PropagationRow] = field(default_factory=list)
    sync_restart_services: list[str] = field(default_factory=list)
    charter_reload: bool = False
    skipped_lines: list[str] = field(default_factory=list)
    prose_only_advisory: bool = False


def _closeout_payload(worker_turns: list[dict[str, Any]]) -> dict[str, Any] | None:
    turn = closeout_turn_from_turns(worker_turns)
    if turn is None:
        return None
    try:
        data = json.loads(str(turn.get("body") or "").strip())
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def _paths_from_closeout(payload: dict[str, Any]) -> tuple[str, ...]:
    paths: set[str] = set()
    for field_name in _FILE_FIELDS:
        raw = payload.get(field_name)
        if not isinstance(raw, list):
            continue
        for entry in raw:
            if isinstance(entry, str) and entry:
                paths.add(entry)
    return tuple(sorted(paths))


def _slug_for_path(path: str) -> str | None:
    return slug_for_service_path(path)


def _resolve_libs_path(path: str) -> tuple[tuple[str, ...], list[str]]:
    """Resolve a ``libs/`` edit to ``(services_to_restart, deferral_lines)``.

    Declared manifest ownership is the actor. Import-graph inference applies only when
    declaration is absent, and auto-restarts only at cardinality 1 (invariant 4).
    """
    if lib_name_for_path(path) is None:
        return (), []
    declared = declared_services_for_lib_path(path)
    if declared:
        return declared, []
    inferred = services_for_lib_path(path, prefixes=path_prefixes())
    if not inferred:
        return (), [f"libs_touched: {path} (no importing service resolved)"]
    if len(inferred) == 1:
        return inferred, []
    joined = ", ".join(inferred)
    return (), [f"unresolved: {path} fans out to {joined} — pick before restart"]


def _slugs_from_residue_lines(lines: list[str]) -> tuple[list[str], list[str]]:
    services: list[str] = []
    skipped: list[str] = []
    seen: set[str] = set()
    for line in lines:
        text = str(line or "").strip()
        if not text:
            continue
        match = _SYNC_RESTART_SLUG_RE.match(text)
        if match:
            slug = match.group(1).lower()
            if slug not in seen:
                seen.add(slug)
                services.append(slug)
            continue
        if text.startswith(("install_plugin:", "libs_touched:", "unresolved:")):
            skipped.append(text)
    return services, skipped


def plan_propagation(worker_turns: list[dict[str, Any]]) -> PropagationPlan | None:
    """Build a propagation plan from the latest worker closeout, or None."""
    payload = _closeout_payload(worker_turns)
    if payload is None:
        return None

    paths = _paths_from_closeout(payload)
    charter_reload = any(path.startswith(_CHARTER_RUNNER_PREFIX) for path in paths)

    rows, skipped, prose_only = rows_from_closeout_payload(payload)
    services = [row.service for row in rows]
    if not services:
        raw_residue = payload.get("propagation_residue")
        lines: list[str] = []
        if isinstance(raw_residue, list):
            lines = [str(item) for item in raw_residue if isinstance(item, str) and item]
        services, skipped = _slugs_from_residue_lines(lines)
        if not services:
            lib_paths = [
                path for path in paths if path.startswith("libs/") and path.endswith(".py")
            ]
            for line in lines:
                match = _SYNC_RESTART_SLUG_RE.match(str(line or "").strip())
                if not match:
                    continue
                for audit_line in audit_sync_restart_slug(match.group(1).lower(), lib_paths):
                    if audit_line not in skipped:
                        skipped.append(audit_line)
            for path in paths:
                slug = _slug_for_path(path)
                if slug is not None:
                    if slug not in services:
                        services.append(slug)
                    continue
                lib_slugs, deferrals = _resolve_libs_path(path)
                for lib_slug in lib_slugs:
                    if lib_slug not in services:
                        services.append(lib_slug)
                for deferral in deferrals:
                    if deferral not in skipped:
                        skipped.append(deferral)

    if not rows and not services and not charter_reload and not skipped:
        return None

    if not rows and services:
        code_ref_str = resolve_code_ref(payload)
        rows = [
            PropagationRow(
                service=slug,
                code_ref=code_ref_str,
            )
            for slug in services
        ]

    return PropagationPlan(
        rows=rows,
        sync_restart_services=services or [row.service for row in rows],
        charter_reload=charter_reload,
        skipped_lines=skipped,
        prose_only_advisory=prose_only,
    )


def giw_i2_clear(*, queue_snapshot: dict[str, Any] | None = None) -> tuple[bool, str]:
    """Return whether GIW restart is permitted under I2 (no in-flight closeout relay)."""
    snapshot = queue_snapshot if queue_snapshot is not None else _fetch_json(_GIW_QUEUE_URL)
    if snapshot is None:
        return False, "i2_queue_unreachable"
    claimed = int(snapshot.get("claimed") or 0)
    pending = int(snapshot.get("pending") or 0)
    if claimed > 0:
        return False, "i2_inflight_closeout"
    if pending > 0:
        return False, "i2_pending_closeout"
    return True, "ok"


def row_may_fire_at_harvest(row: OpenPropagationProjection) -> tuple[bool, str]:
    """Apply safe-window matrix for charter harvest firing."""
    if row.safe_window == "standalone_ok":
        return True, "standalone_ok_allowed_at_harvest"
    if row.safe_window in ("harvest", "drain_required"):
        return True, f"{row.safe_window}_harvest_window"
    return False, f"safe_window_{row.safe_window}_defer"


def giw_restart_precondition(
    row: OpenPropagationProjection,
    *,
    queue_snapshot: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    """GIW rows require explicit relay-loss hazard plus I2 before harvest may fire."""
    if row.service != "git_integration_worker":
        return True, "ok"
    if not (row.hazard or "").strip():
        return False, "giw_requires_relay_loss_hazard"
    return giw_i2_clear(queue_snapshot=queue_snapshot)


def _fetch_json(url: str, *, timeout_s: float = 3.0) -> dict[str, Any] | None:
    global _probe_client
    try:
        client = _probe_client or httpx.Client(timeout=timeout_s)
        resp = client.get(url)
        if resp.status_code != 200:
            return None
        data = resp.json()
        return data if isinstance(data, dict) else None
    except (httpx.HTTPError, ValueError, OSError):
        return None


def probe_process_live(service: str) -> dict[str, Any] | None:
    """Fetch health/liveness JSON for proof-of-live closure."""
    from services.git_integration_worker.cursor_auto.propagation_probe import (
        probe_process_live as _probe,
    )

    return _probe(service)


def probe_for_projection(row: OpenPropagationProjection) -> dict[str, Any] | None:
    """Probe closure surface for one open ledger row via proof_class registry."""
    result = dispatch_proof_probe(_projection_to_row(row))
    if result.error is not None:
        return None
    return result.payload


def dispatch_for_projection(row: OpenPropagationProjection) -> ProbeDispatchResult:
    """Full dispatch result including unsupported-class errors."""
    return dispatch_proof_probe(_projection_to_row(row))


def _projection_to_row(row: OpenPropagationProjection) -> PropagationRow:
    proof = row.proof or default_proof(row.service, row.proof_class)
    return PropagationRow(
        service=row.service,
        code_ref=row.code_ref,
        safe_window=row.safe_window,
        proof=proof,
        proof_class=row.proof_class,  # type: ignore[arg-type]
        proof_class_requested=row.proof_class,  # type: ignore[arg-type]
    )


def proof_matches(
    row: OpenPropagationProjection,
    payload: dict[str, Any] | None,
    *,
    before: dict[str, Any] | None = None,
    settle_not_before_monotonic: float | None = None,
) -> bool:
    """Close predicate: identity-aware proof via shared propagation_probe helper."""
    from services.git_integration_worker.cursor_auto.propagation_probe import (
        proof_observed,
    )

    return proof_observed(
        _projection_to_row(row),
        payload,
        before=before,
        settle_not_before_monotonic=settle_not_before_monotonic,
    )


async def execute_propagation_plan(
    plan: PropagationPlan,
    *,
    root_id: str,
    window_index: int,
) -> dict[str, Any]:
    """Persist rows and run harvest closure — proof closes, not restart status."""
    if _context is None:
        logger.warning(
            "charter propagation skipped root=%s window=%s — no ServiceController",
            root_id,
            window_index,
        )
        return {"status": "skipped", "reason": "no_service_controller"}

    from scripts.model_manager.ui.api_dispatch import sync_restart_charter_harvest

    ctl, event_bus = _context
    if plan.rows:
        upsert_open_rows(plan.rows)

    bump_age_for_open_rows()
    # Open rows are the obligation fire set — not a liveness oracle. Terminal
    # failed events stay out of this list by design; seats asking current
    # liveness use observe_code_ref_live (does not open sqlite).
    open_rows = [
        row for row in list_open_rows() if row.defer_reason != "harvest_wanted"
    ]
    queue_snapshot = _fetch_json(_GIW_QUEUE_URL)

    closed: list[dict[str, Any]] = []
    remaining: list[dict[str, Any]] = []
    escalated: list[dict[str, Any]] = []
    service_results: dict[str, Any] = {}

    for row in open_rows:
        requested_class = row.proof_class
        projection = {
            "row_id": row.row_id,
            "service": row.service,
            "code_ref": row.code_ref,
            "safe_window": row.safe_window,
            "age_in_harvests": row.age_in_harvests,
            "proof_class_requested": requested_class,
        }

        dispatch_before = dispatch_for_projection(row)
        if dispatch_before.error is not None:
            fail_row(
                row.row_id,
                proof_payload={
                    "proof_class_requested": dispatch_before.proof_class_requested,
                    "proof_class_executed": dispatch_before.proof_class_executed,
                },
                reason=dispatch_before.error,
            )
            remaining.append(
                {
                    **projection,
                    "defer_reason": dispatch_before.error,
                    "proof_class_executed": None,
                    "disposition": "failed_proof_class_unsupported",
                }
            )
            if row.age_in_harvests >= 2:
                escalated.append(
                    {**projection, "defer_reason": dispatch_before.error}
                )
            continue

        before = dispatch_before.payload

        # Pre-fire re-observe (F4 census #13): when a newer live version already
        # satisfies the owed code_ref (ancestor), stop chasing this obligation
        # without burning a restart. Exact match still fires — harvest close
        # requires process-identity delta after sync_restart, not merely a
        # matching code_version on the outgoing generation.
        live = observe_code_ref_live(
            row.service,
            row.code_ref,
            probe=lambda _service, _payload=before: (
                _payload if isinstance(_payload, dict) else None
            ),
        )
        if live.answer == "yes" and live.relation == "ancestor":
            pre = settle_open_row(
                row,
                lambda _service, _payload=before: _payload,
                defer_if_unreachable=True,
            )
            remaining.append(
                {
                    **projection,
                    "defer_reason": pre.detail,
                    "proof_class_executed": dispatch_before.proof_class_executed,
                    "disposition": "pre_fire_ancestry_satisfied",
                    "proof": before,
                    "liveness": live.reason,
                }
            )
            if row.age_in_harvests >= 2:
                escalated.append({**projection, "defer_reason": pre.detail})
            continue

        may_fire, window_reason = row_may_fire_at_harvest(row)
        i2_ok, i2_reason = giw_restart_precondition(row, queue_snapshot=queue_snapshot)
        if not may_fire or not i2_ok:
            defer = i2_reason if not i2_ok else window_reason
            set_defer_reason(row.row_id, defer)
            remaining.append(
                {
                    **projection,
                    "defer_reason": defer,
                    "proof_class_executed": dispatch_before.proof_class_executed,
                }
            )
            if row.age_in_harvests >= 2:
                escalated.append({**projection, "defer_reason": defer})
            continue

        try:
            outcome = await sync_restart_charter_harvest(
                ctl, row.service, event_bus=event_bus
            )
            service_results[row.service] = outcome
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "charter propagation sync_restart failed service=%s root=%s window=%s",
                row.service,
                root_id,
                window_index,
            )
            defer = f"sync_restart_error:{type(exc).__name__}"
            set_defer_reason(row.row_id, defer)
            remaining.append(
                {
                    **projection,
                    "defer_reason": defer,
                    "proof_class_executed": dispatch_before.proof_class_executed,
                }
            )
            continue

        dispatch_after = dispatch_for_projection(row)
        if dispatch_after.error is not None:
            fail_row(
                row.row_id,
                proof_payload={
                    "proof_class_requested": dispatch_after.proof_class_requested,
                    "proof_class_executed": dispatch_after.proof_class_executed,
                },
                reason=dispatch_after.error,
            )
            remaining.append(
                {
                    **projection,
                    "defer_reason": dispatch_after.error,
                    "proof_class_executed": None,
                    "disposition": "failed_proof_class_unsupported",
                }
            )
            continue

        live_after = dispatch_after.payload
        executed_class = dispatch_after.proof_class_executed
        class_diverged = executed_class != requested_class
        proof_ok = (
            not class_diverged
            and proof_matches(row, live_after, before=before)
        )
        close_payload = {
            **(live_after or {}),
            "proof_class_requested": requested_class,
            "proof_class_executed": executed_class,
        }
        if class_diverged:
            defer = (
                f"proof_class_diverged:requested={requested_class}"
                f":executed={executed_class}"
            )
            set_defer_reason(row.row_id, defer)
            remaining.append(
                {
                    **projection,
                    "defer_reason": defer,
                    "proof_class_executed": executed_class,
                    "disposition": "failed_proof_class_diverged",
                    "proof": live_after,
                }
            )
            if row.age_in_harvests >= 2:
                escalated.append({**projection, "defer_reason": defer})
        elif proof_ok:
            close_row(row.row_id, proof_payload=close_payload)
            closed.append(
                {
                    **projection,
                    "proof": live_after,
                    "proof_before": before,
                    "proof_class_executed": executed_class,
                    "disposition": "closed",
                }
            )
        else:
            defer = "proof_not_observed_after_restart"
            set_defer_reason(row.row_id, defer)
            remaining.append(
                {
                    **projection,
                    "defer_reason": defer,
                    "proof_class_executed": executed_class,
                    "disposition": "deferred_proof_not_observed",
                    "proof": live_after,
                }
            )
            if row.age_in_harvests >= 2:
                escalated.append({**projection, "defer_reason": defer})

    results: dict[str, Any] = {
        "status": "ok",
        "root": root_id,
        "window_index": window_index,
        "services": service_results,
        "skipped_lines": list(plan.skipped_lines),
        "closed": closed,
        "remaining": remaining,
        "escalated": escalated,
        "scoreboard": scoreboard_projection(),
        "prose_only_advisory": plan.prose_only_advisory,
    }

    try:
        board_path = scoreboard_path_for_root(root_id)
        if board_path.is_file():
            write_scoreboard_open_rows(board_path, results["scoreboard"])
    except OSError:
        logger.exception(
            "charter scoreboard open-row render failed root=%s window=%s",
            root_id,
            window_index,
        )

    if plan.charter_reload:
        schedule_charter_reload()
        results["charter_reload"] = {
            "status": "scheduled",
            "reason": "deferred_until_tick_slice_end",
        }

    if service_results and any(
        outcome.get("status") not in ("ok", "deferred")
        for outcome in service_results.values()
    ):
        results["status"] = "partial"

    return results


async def maybe_execute_window_propagation(
    *,
    root_id: str,
    window_index: int,
    worker_turns: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Harvest hook: execute propagation when closeout requires it."""
    plan = plan_propagation(worker_turns)
    if plan is None:
        return None

    from scripts.model_manager import observation_event_charter as events

    await events.emit_manage_charter_tick_propagation_started(
        root=root_id,
        window_index=window_index,
        services=list(plan.sync_restart_services),
        charter_reload=plan.charter_reload,
        rows=[row.model_dump(mode="json") for row in plan.rows],
    )
    results = await execute_propagation_plan(
        plan, root_id=root_id, window_index=window_index
    )
    await events.emit_manage_charter_tick_propagation_completed(
        root=root_id,
        window_index=window_index,
        results=results,
    )
    if results.get("escalated"):
        await events.emit_manage_charter_tick_propagation_escalated(
            root=root_id,
            window_index=window_index,
            escalated=list(results["escalated"]),
        )
    return results


def set_probe_client_for_tests(client: httpx.Client | None) -> None:
    """Inject httpx client for unit tests."""
    global _probe_client
    _probe_client = client


__all__ = [
    "PROOF_PROBE_REGISTRY",
    "ProbeDispatchResult",
    "PropagationPlan",
    "consume_pending_charter_reload",
    "dispatch_for_projection",
    "dispatch_proof_probe",
    "execute_propagation_plan",
    "giw_i2_clear",
    "giw_restart_precondition",
    "install_propagation_context",
    "maybe_execute_window_propagation",
    "plan_propagation",
    "probe_process_live",
    "proof_class_unsupported_detail",
    "proof_matches",
    "registered_proof_classes",
    "row_may_fire_at_harvest",
    "schedule_charter_reload",
    "set_probe_client_for_tests",
]
