"""Charter harvest propagation — drain-aware service restart after window close.

When a worker closeout carries ``propagation_residue`` (landed≠live), the charter
tick executes ``sync_restart`` for each mapped service and ``charter_reload`` when
charter-runner sources changed. Runs at harvest time (worker already exited) so
git-integration-worker restarts use the blocking drain supervisor.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .bus_client import closeout_turn_from_turns

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

_context: tuple[ServiceController, EventBus | None] | None = None
_pending_charter_reload: bool = False


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

    sync_restart_services: list[str] = field(default_factory=list)
    charter_reload: bool = False
    skipped_lines: list[str] = field(default_factory=list)


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

    raw_residue = payload.get("propagation_residue")
    lines: list[str] = []
    if isinstance(raw_residue, list):
        lines = [str(item) for item in raw_residue if isinstance(item, str) and item]

    services, skipped = _slugs_from_residue_lines(lines)
    if not services:
        for path in paths:
            slug = _slug_for_path(path)
            if slug and slug not in services:
                services.append(slug)

    if not services and not charter_reload and not skipped:
        return None
    return PropagationPlan(
        sync_restart_services=services,
        charter_reload=charter_reload,
        skipped_lines=skipped,
    )


async def execute_propagation_plan(
    plan: PropagationPlan,
    *,
    root_id: str,
    window_index: int,
) -> dict[str, Any]:
    """Run propagation actions; return per-action results."""
    if _context is None:
        logger.warning(
            "charter propagation skipped root=%s window=%s — no ServiceController",
            root_id,
            window_index,
        )
        return {"status": "skipped", "reason": "no_service_controller"}

    from scripts.model_manager.ui.api_dispatch import sync_restart_charter_harvest

    ctl, event_bus = _context
    results: dict[str, Any] = {
        "status": "ok",
        "root": root_id,
        "window_index": window_index,
        "services": {},
        "skipped_lines": list(plan.skipped_lines),
    }

    if plan.charter_reload:
        reload_pending = True
    else:
        reload_pending = False

    for service in plan.sync_restart_services:
        try:
            outcome = await sync_restart_charter_harvest(
                ctl, service, event_bus=event_bus
            )
            results["services"][service] = outcome
            if outcome.get("status") not in ("ok", "deferred"):
                results["status"] = "partial"
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "charter propagation sync_restart failed service=%s root=%s window=%s",
                service,
                root_id,
                window_index,
            )
            results["services"][service] = {
                "status": "error",
                "error": f"{type(exc).__name__}: {exc}",
            }
            results["status"] = "partial"

    if reload_pending:
        schedule_charter_reload()
        results["charter_reload"] = {
            "status": "scheduled",
            "reason": "deferred_until_tick_slice_end",
        }

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
    )
    results = await execute_propagation_plan(
        plan, root_id=root_id, window_index=window_index
    )
    await events.emit_manage_charter_tick_propagation_completed(
        root=root_id,
        window_index=window_index,
        results=results,
    )
    return results


__all__ = [
    "PropagationPlan",
    "consume_pending_charter_reload",
    "execute_propagation_plan",
    "install_propagation_context",
    "maybe_execute_window_propagation",
    "plan_propagation",
    "schedule_charter_reload",
]
