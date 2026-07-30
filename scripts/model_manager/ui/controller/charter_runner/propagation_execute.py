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
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import httpx
from charter_runner_store.propagation_ledger import (
    OpenPropagationProjection,
    bump_age_for_open_rows,
    close_row,
    list_open_rows,
    scoreboard_projection,
    set_defer_reason,
    upsert_open_rows,
)
from deploy_identity.mcp_health_probe_url import resolve_mcp_health_probe_url
from implement_admission.propagation_row import (
    PropagationRow,
    resolve_code_ref,
    rows_from_closeout_payload,
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

_SERVICE_PREFIXES: tuple[tuple[str, str], ...] = (
    ("services/agent-bus/", "agent_bus"),
    ("services/cortex-api/", "cortex_api"),
    ("services/event-service/", "event_service"),
    ("services/git_integration_worker/", "git_integration_worker"),
    ("services/mcp-server/", "mcp"),
    ("services/rag/", "rag"),
    ("services/universal_cloud_proxy/", "cloud_proxy"),
    ("services/_universal-llm-gateway/", "gateway"),
    ("services/universal-stargate/", "stargate"),
)

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
    for prefix, slug in _SERVICE_PREFIXES:
        if path.startswith(prefix) and path.endswith(".py"):
            return slug
    return None


def _resolve_libs_path(path: str) -> tuple[str | None, str | None]:
    """Resolve a ``libs/`` edit to ``(service_to_restart, residue_line)``.

    A lib edit resolving to exactly one service is unambiguous and restarts it. Anything
    else is reported rather than acted on: import-graph closure at package granularity is
    too coarse to license restarting several services, and silently dropping the path is
    the arc-6386 failure. Naming the candidates puts the choice at the operator seat.
    """
    if lib_name_for_path(path) is None:
        return None, None
    candidates = services_for_lib_path(path, prefixes=_SERVICE_PREFIXES)
    if not candidates:
        return None, f"libs_touched: {path} (no importing service resolved)"
    if len(candidates) == 1:
        return candidates[0], None
    joined = ", ".join(candidates)
    return None, f"unresolved: {path} fans out to {joined} — pick before restart"


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
            for path in paths:
                slug = _slug_for_path(path)
                if slug is not None:
                    if slug not in services:
                        services.append(slug)
                    continue
                lib_slug, line = _resolve_libs_path(path)
                if lib_slug is not None and lib_slug not in services:
                    services.append(lib_slug)
                if line is not None and line not in skipped:
                    skipped.append(line)

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
    """GIW rows require I2 regardless of safe_window class."""
    if row.service != "git_integration_worker":
        return True, "ok"
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
    if service == "git_integration_worker":
        return _fetch_json(_GIW_LIVENESS_URL)
    if service == "mcp":
        return _fetch_json(resolve_mcp_health_probe_url())
    return None


def proof_matches(row: OpenPropagationProjection, payload: dict[str, Any] | None) -> bool:
    """Close predicate: observed code_version equals row code_ref."""
    if payload is None:
        return False
    observed = payload.get("code_version")
    return isinstance(observed, str) and observed == row.code_ref


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
    open_rows = list_open_rows()
    queue_snapshot = _fetch_json(_GIW_QUEUE_URL)

    closed: list[dict[str, Any]] = []
    remaining: list[dict[str, Any]] = []
    escalated: list[dict[str, Any]] = []
    service_results: dict[str, Any] = {}

    for row in open_rows:
        projection = {
            "row_id": row.row_id,
            "service": row.service,
            "code_ref": row.code_ref,
            "safe_window": row.safe_window,
            "age_in_harvests": row.age_in_harvests,
        }
        live_payload = probe_process_live(row.service)
        if proof_matches(row, live_payload):
            close_row(row.row_id, proof_payload=live_payload or {})
            closed.append({**projection, "proof": live_payload})
            continue

        may_fire, window_reason = row_may_fire_at_harvest(row)
        i2_ok, i2_reason = giw_restart_precondition(row, queue_snapshot=queue_snapshot)
        if not may_fire or not i2_ok:
            defer = i2_reason if not i2_ok else window_reason
            set_defer_reason(row.row_id, defer)
            remaining.append({**projection, "defer_reason": defer})
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
            remaining.append({**projection, "defer_reason": defer})
            continue

        live_after = probe_process_live(row.service)
        if proof_matches(row, live_after):
            close_row(row.row_id, proof_payload=live_after or {})
            closed.append({**projection, "proof": live_after})
        else:
            defer = "proof_not_observed_after_restart"
            set_defer_reason(row.row_id, defer)
            remaining.append({**projection, "defer_reason": defer})
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
    "PropagationPlan",
    "consume_pending_charter_reload",
    "execute_propagation_plan",
    "giw_i2_clear",
    "giw_restart_precondition",
    "install_propagation_context",
    "maybe_execute_window_propagation",
    "plan_propagation",
    "probe_process_live",
    "proof_matches",
    "row_may_fire_at_harvest",
    "schedule_charter_reload",
    "set_probe_client_for_tests",
]
