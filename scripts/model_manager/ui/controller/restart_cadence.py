"""Periodic non-force sync_restart arming when sealed code lags master on load surface.

``RestartCadenceLoop`` runs inside manage after ``manage.sock`` binds. Every
``RESTART_CADENCE_INTERVAL_S`` it compares each ``SYNC_RESTART_SERVICES`` slug's
live ``code_version`` to ``master``, diffs Python load-surface paths, and calls
``api_dispatch.execute`` with ``force=False`` when drift warrants a drain restart.
It never starts stopped services and skips slugs that already have pending intents.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
from collections.abc import Awaitable, Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from implement_admission.service_lib_ownership import (
    serving_services_for_lib_path,
    slug_for_service_path,
)

from . import restart_cadence_record as cadence_record
from .restart_intent_store import RestartIntentStore

if TYPE_CHECKING:
    from .service_ctl.core import ServiceController

logger = logging.getLogger(__name__)

RESTART_CADENCE_INTERVAL_S = 900.0
_MASTER_REF = "master"
_CALLER_AGENT = "restart_cadence"


ExecuteFn = Callable[
    [Any, str, str, dict[str, Any]], Awaitable[dict[str, Any]]
]
ProbeFn = Callable[[str], dict[str, Any] | None]
RevParseFn = Callable[[str], str | None]
DiffFn = Callable[[str, str], list[str] | None]
PendingFn = Callable[[], frozenset[str]]


def path_affects_service_load_surface(path: str, service: str) -> bool:
    """True when a repo-relative diff path should reload ``service`` on restart."""
    if not path.endswith(".py"):
        return False
    if slug_for_service_path(path) == service:
        return True
    return service in serving_services_for_lib_path(path)


def filter_load_surface_paths(
    paths: Iterable[str], service: str
) -> list[str]:
    """Keep only ``.py`` diff paths whose service or lib ownership includes ``service``."""
    return [p for p in paths if path_affects_service_load_surface(p, service)]


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _default_rev_parse(root: Path, ref: str) -> str | None:
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "rev-parse", ref],
            capture_output=True,
            text=True,
            timeout=30.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    sha = proc.stdout.strip()
    return sha or None


def _default_diff_name_only(root: Path, old_sha: str, new_sha: str) -> list[str] | None:
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "diff", "--name-only", old_sha, new_sha],
            capture_output=True,
            text=True,
            timeout=60.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def _default_probe(controller: ServiceController, service: str) -> dict[str, Any] | None:
    from scripts.model_manager.ui.api_dispatch import _check_one
    from scripts.model_manager.ui.model.service_state import ServiceStatus
    from services.git_integration_worker.relay.propagation_probe import (
        probe_process_live,
    )

    try:
        info = _check_one(controller.service_state, service)
    except Exception:
        return None
    if info.status != ServiceStatus.RUNNING:
        return None
    if info.pid is None:
        return None
    health = probe_process_live(service)
    if not isinstance(health, dict):
        return None
    raw_version = health.get("code_version")
    if not isinstance(raw_version, str) or not raw_version.strip():
        return None
    return {
        "status": "running",
        "pid": info.pid,
        "code_version": raw_version.strip(),
    }


def _default_pending_services() -> frozenset[str]:
    return frozenset(
        intent.service for intent in RestartIntentStore.instance().pending_intents()
    )


async def _default_execute(
    controller: ServiceController,
    service: str,
    master_sha: str,
) -> dict[str, Any]:
    from scripts.model_manager.ui.api_dispatch import execute

    return await execute(
        controller,
        "sync_restart",
        service,
        {
            "force": False,
            "code_ref": master_sha,
            "caller_agent": _CALLER_AGENT,
        },
    )


class RestartCadenceLoop:
    """Manage background loop: compare sealed code to master and arm idle sync_restart.

    Started after ``manage.sock`` binds; cancelled on TUI unmount. Each tick
    reconciles cadence audit rows, probes fleet services, and calls injectable
    git/execute helpers with production defaults.
    """

    def __init__(
        self,
        controller: ServiceController,
        *,
        interval_s: float = RESTART_CADENCE_INTERVAL_S,
        rev_parse: RevParseFn | None = None,
        diff_name_only: DiffFn | None = None,
        probe_service: ProbeFn | None = None,
        pending_services: PendingFn | None = None,
        execute: ExecuteFn | None = None,
        db_path: Path | None = None,
    ) -> None:
        self._controller = controller
        self._interval_s = interval_s
        self._root = controller.root
        self._rev_parse = rev_parse or (lambda ref: _default_rev_parse(self._root, ref))
        self._diff_name_only = diff_name_only or (
            lambda old, new: _default_diff_name_only(self._root, old, new)
        )
        self._probe_service = probe_service or (
            lambda service: _default_probe(controller, service)
        )
        self._pending_services = pending_services or _default_pending_services
        if execute is None:

            async def _bound_execute(
                ctl: ServiceController,
                _method: str,
                service: str,
                params: dict[str, Any],
            ) -> dict[str, Any]:
                return await _default_execute(ctl, service, str(params["code_ref"]))

            self._execute = _bound_execute
        else:
            self._execute = execute
        self._db_path = db_path
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        """Schedule the cadence loop unless a task is already running."""
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._loop(), name="restart-cadence")

    async def stop(self) -> None:
        """Cancel the cadence task and await teardown."""
        task = self._task
        self._task = None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _loop(self) -> None:
        await self._tick()
        while True:
            await asyncio.sleep(self._interval_s)
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("restart cadence tick failed")

    async def _tick(self) -> None:
        from scripts.model_manager.ui.api_dispatch import SYNC_RESTART_SERVICES

        await asyncio.to_thread(self._reconcile_pending_records)
        master_sha = await asyncio.to_thread(self._rev_parse, _MASTER_REF)
        if not master_sha:
            return
        pending = await asyncio.to_thread(self._pending_services)
        for service in sorted(SYNC_RESTART_SERVICES):
            if service in pending:
                continue
            try:
                await self._tick_service(service, master_sha)
            except Exception:
                logger.exception("restart cadence service tick failed for %s", service)

    def _reconcile_pending_records(self) -> None:
        from scripts.model_manager.ui.controller.restart_intent_consumer import (
            project_restart_intent_last,
        )

        for row in cadence_record.list_pending_new_pid(db_path=self._db_path):
            intent = cadence_record.load_intent(row.intent_id)
            if intent is None:
                cadence_record.delete_record(row.intent_id, db_path=self._db_path)
                continue
            projection = project_restart_intent_last(intent)
            if projection is None:
                continue
            if not projection.get("executed"):
                cadence_record.delete_record(row.intent_id, db_path=self._db_path)
                continue
            probe = self._probe_service(row.service)
            new_pid = probe.get("pid") if isinstance(probe, dict) else None
            if not isinstance(new_pid, int) or new_pid == row.old_pid:
                continue
            cadence_record.fill_new_pid(row.intent_id, new_pid, db_path=self._db_path)

    async def _tick_service(self, service: str, master_sha: str) -> None:
        probe = await asyncio.to_thread(self._probe_service, service)
        if not isinstance(probe, dict):
            return
        if probe.get("status") != "running":
            return
        old_pid = probe.get("pid")
        if not isinstance(old_pid, int):
            return
        sealed_label = probe.get("code_version")
        if not isinstance(sealed_label, str) or not sealed_label.strip():
            return
        sealed_sha = await asyncio.to_thread(self._rev_parse, sealed_label.strip())
        if not sealed_sha:
            return
        if sealed_sha == master_sha:
            return
        diff_paths = await asyncio.to_thread(
            self._diff_name_only, sealed_sha, master_sha
        )
        if diff_paths is None:
            return
        if not filter_load_surface_paths(diff_paths, service):
            return
        result = await self._execute(
            self._controller,
            "sync_restart",
            service,
            {"force": False, "code_ref": master_sha, "caller_agent": _CALLER_AGENT},
        )
        await asyncio.to_thread(
            self._record_execute_outcome,
            service,
            old_pid,
            master_sha,
            result,
        )

    def _record_execute_outcome(
        self,
        service: str,
        old_pid: int,
        master_sha: str,
        result: dict[str, Any],
    ) -> None:
        intent_id = result.get("restart_intent_id") or result.get("intent_id")
        if isinstance(intent_id, str) and intent_id:
            cadence_record.insert_armed(
                intent_id=intent_id,
                service=service,
                old_pid=old_pid,
                code_ref=master_sha,
                armed_at=_now_iso(),
                db_path=self._db_path,
            )
            return
        if result.get("status") == "ok":
            probe = self._probe_service(service)
            new_pid = probe.get("pid") if isinstance(probe, dict) else None
            if isinstance(new_pid, int) and new_pid != old_pid:
                synthetic_id = f"cadence-immediate-{service}-{old_pid}-{new_pid}"
                cadence_record.insert_completed(
                    intent_id=synthetic_id,
                    service=service,
                    old_pid=old_pid,
                    new_pid=new_pid,
                    code_ref=master_sha,
                    db_path=self._db_path,
                )
