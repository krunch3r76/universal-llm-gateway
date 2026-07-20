"""Manage-hosted charter-runner tick.

Periodic supervisor that watches enrolled standing roots and admits one default
Grok 4.5 High (``cursor/grok-4.5``, effort=high, fast=false) cursor-sdk window
per eligible root. CHECKPOINT on the charter root clears in-flight.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from universal_logging import get_logger

from scripts.model_manager import observation_event as events
from scripts.model_manager.ui.controller.service_config import build_mcp_env
from scripts.model_manager.ui.controller.shutdown_gate import ManageShutdownGate
from scripts.model_manager.ui.model.service_state import ServiceState, ServiceStatus

from . import bus_client, dispatch_client, window_log
from .caps import CapStore
from .eligibility import (
    ADMISSION_SUBJECT_PREFIX,
    CHECKPOINT_PREFIX,
    Decision,
    evaluate_root,
)
from .materializer import handoff_subject, materialize_resume_packet

logger = get_logger(__name__)

_DEFAULT_TICK_INTERVAL_S = 60.0
_ACTIVITY = "charter_tick"
# Soft remind only — attended handoffs may sit until the operator opens IDE.
_WAITING_OPEN_REMIND_S = 900.0
_ENV_KEYS = ("AGENT_BUS_TOKEN",)


def ensure_charter_tick_env(workspace_root: Path) -> None:
    """Overlay bus auth into the manage process env (token is not ambient)."""
    merged = build_mcp_env(workspace_root)
    for key in _ENV_KEYS:
        if merged.get(key):
            os.environ[key] = merged[key]


class CharterRunnerTickLoop:
    """Async supervisor: scan enrolled roots + admit Composer handoff windows."""

    def __init__(
        self,
        *,
        service_state: ServiceState,
        shutdown_gate: ManageShutdownGate,
        workspace_root: Path | None = None,
        tick_interval_s: float = _DEFAULT_TICK_INTERVAL_S,
        caps: CapStore | None = None,
        on_admit: Callable[[str], None] | None = None,
    ) -> None:
        self._service_state = service_state
        self._shutdown_gate = shutdown_gate
        self._workspace_root = workspace_root
        self._tick_interval_s = tick_interval_s
        self._caps = caps or CapStore()
        self._on_admit = on_admit
        self._loop_task: asyncio.Task[None] | None = None
        self._reminded: set[str] = set()

    async def start(self) -> None:
        if self._loop_task is not None:
            return
        if self._workspace_root is not None:
            ensure_charter_tick_env(self._workspace_root)
        self._loop_task = asyncio.create_task(self._run_loop())
        await events.emit_manage_charter_tick_started()

    async def stop(self) -> None:
        loop_task = self._loop_task
        if loop_task is None:
            return
        loop_task.cancel()
        try:
            await loop_task
        except asyncio.CancelledError:
            pass
        self._loop_task = None
        await events.emit_manage_charter_tick_stopped()

    async def _run_loop(self) -> None:
        try:
            while True:
                if not self._services_healthy():
                    await asyncio.sleep(self._tick_interval_s)
                    continue
                self._shutdown_gate.set_activity(_ACTIVITY, True)
                try:
                    await self._tick_once()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 — tick errors are non-fatal
                    logger.exception("charter tick failed")
                    await events.emit_manage_charter_tick_error(reason=str(exc))
                finally:
                    self._shutdown_gate.set_activity(_ACTIVITY, False)
                await asyncio.sleep(self._tick_interval_s)
        except asyncio.CancelledError:
            self._shutdown_gate.set_activity(_ACTIVITY, False)
            raise

    def _services_healthy(self) -> bool:
        cortex = self._service_state.check_cortex_api()
        if cortex.status != ServiceStatus.RUNNING:
            return False
        bus = self._service_state.check_agent_bus()
        return bus.status == ServiceStatus.RUNNING

    async def _tick_once(self) -> None:
        roots = await bus_client.list_enrolled_roots()
        admitted = 0
        for thread in roots:
            root_id = str(thread.get("id") or "")
            if not root_id:
                continue
            turns = await bus_client.fetch_turns(root_id)
            await self._harvest_completed_windows(root_id, turns)
            decision = evaluate_root(root_id, turns, self._caps)
            if decision.eligible:
                if await self._admit_window(decision, turns):
                    admitted += 1
            elif decision.reason == "window_in_flight":
                await self._handle_waiting_open(decision)
        await events.emit_manage_charter_tick_scanned(
            roots=len(roots), admitted=admitted
        )

    async def _admit_window(self, decision: Decision, turns: list[dict]) -> bool:
        root_id = decision.root_id
        assert decision.parsed is not None and decision.checkpoint is not None
        if self._workspace_root is None:
            raise RuntimeError(
                "charter-runner requires workspace_root for handoff packets"
            )
        window_index = _count_admissions(turns) + 1
        packet = materialize_resume_packet(
            root_id,
            decision.parsed,
            scoreboard_uri=decision.parsed.scoreboard_uri,
            window_index=window_index,
        )
        # Fire first — a failed handoff must not leave an orphaned in-flight pointer.
        result = await dispatch_client.fire_window(
            root_id,
            packet,
            workspace_root=self._workspace_root,
            window_index=window_index,
            subject=handoff_subject(root_id, window_index),
        )
        worker_thread = str(result.get("thread_id") or "")
        packet_path = str(result.get("packet_path") or "")
        push = str(result.get("push_reminder") or "")
        now_iso = datetime.now(UTC).isoformat()
        await bus_client.post_admission_pointer(
            root_id,
            window_index=window_index,
            posted_at_iso=now_iso,
            worker_thread=worker_thread,
            packet_path=packet_path,
        )
        self._caps.record_admit(root_id)
        self._reminded.discard(root_id)
        await events.emit_manage_charter_tick_admitted(
            root=root_id,
            dispatch_id=str(result.get("dispatch_id") or worker_thread),
            worker_thread=worker_thread,
        )
        try:
            window_log.append_admit(
                root_id=root_id,
                window_index=window_index,
                worker_thread=worker_thread,
                packet_path=packet_path,
                packet_text=packet,
                push_reminder=push,
                dispatch_id=str(result.get("dispatch_id") or ""),
            )
            # Record executor bind in the numbered transcript.
            executor = result.get("executor") or {}
            if executor and worker_thread:
                from .window_log import worker_transcript_path

                note = (
                    f"\n--- executor ---\n"
                    f"seat={executor.get('seat')} model={executor.get('model')} "
                    f"knobs={executor.get('model_knobs')} "
                    f"contract={executor.get('contract')}\n"
                )
                path = worker_transcript_path(worker_thread)
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a", encoding="utf-8") as fh:
                    fh.write(note)
        except Exception:  # noqa: BLE001 — transcript must not kill the tick
            logger.exception("charter-runner window_log append_admit failed")
        msg = (
            f"charter-runner: admitted {worker_thread} for root {root_id}"
            f" (cursor/grok-4.5 effort=high)"
        )
        if push:
            msg += f" — {push}"
        if self._on_admit is not None:
            try:
                self._on_admit(msg)
            except Exception:  # noqa: BLE001 — notify must not kill the tick
                logger.exception("charter-runner on_admit notify failed")
        return True

    async def _harvest_completed_windows(
        self, root_id: str, turns: list[dict]
    ) -> None:
        """Append worker turns + CHECKPOINT for windows that closed since last tick."""
        for admission, checkpoint in _completed_windows(turns):
            meta = window_log.parse_admission_meta(
                str(admission.get("body") or "")
            )
            try:
                window_index = int(meta.get("window") or 0)
            except (TypeError, ValueError):
                window_index = 0
            if window_index <= 0 or window_log.already_harvested(root_id, window_index):
                continue
            worker_thread = str(meta.get("worker_thread") or "")
            worker_turns: list[dict] = []
            if worker_thread:
                try:
                    worker_turns = await bus_client.fetch_turns(worker_thread)
                except Exception:  # noqa: BLE001 — closeout still records CHECKPOINT
                    logger.exception(
                        "charter-runner failed fetching worker %s", worker_thread
                    )
            worker_closed: bool | None = None
            if worker_thread:
                try:
                    await bus_client.close_worker_thread(
                        worker_thread,
                        summary=(
                            f"charter-runner window {window_index} complete — "
                            f"root {root_id} CHECKPOINT "
                            f"{checkpoint.get('subject') or ''}"
                        ),
                    )
                    worker_closed = True
                except Exception:  # noqa: BLE001 — transcript still records failure
                    worker_closed = False
                    logger.exception(
                        "charter-runner failed closing worker %s", worker_thread
                    )
            try:
                window_log.append_closeout(
                    root_id=root_id,
                    window_index=window_index,
                    worker_thread=worker_thread,
                    checkpoint_subject=str(checkpoint.get("subject") or ""),
                    checkpoint_body=str(checkpoint.get("body") or ""),
                    worker_turns=worker_turns,
                    worker_closed=worker_closed,
                )
            except Exception:  # noqa: BLE001 — transcript must not kill the tick
                logger.exception("charter-runner window_log append_closeout failed")

    async def _handle_waiting_open(self, decision: Decision) -> None:
        """Soft remind only — never auto-fail while waiting for operator open."""
        adm = decision.admission_turn or {}
        posted_at = _admission_posted_at(adm)
        if posted_at is None:
            return
        age = (datetime.now(UTC) - posted_at).total_seconds()
        if age < _WAITING_OPEN_REMIND_S:
            return
        if decision.root_id in self._reminded:
            return
        self._reminded.add(decision.root_id)
        await events.emit_manage_charter_tick_waiting_open(
            root=decision.root_id, age_s=int(age)
        )


def _count_admissions(turns: list[dict]) -> int:
    prefix = ADMISSION_SUBJECT_PREFIX.upper()
    return sum(
        1 for t in turns if str(t.get("subject") or "").upper().startswith(prefix)
    )


def _turn_number(turn: dict) -> int:
    try:
        return int(turn.get("turn_number") or 0)
    except (TypeError, ValueError):
        return 0


def _completed_windows(turns: list[dict]) -> list[tuple[dict, dict]]:
    """Pairs of (admission, following CHECKPOINT) for closed windows."""
    ordered = sorted(turns, key=_turn_number)
    pairs: list[tuple[dict, dict]] = []
    adm_prefix = ADMISSION_SUBJECT_PREFIX.upper()
    cp_prefix = CHECKPOINT_PREFIX.upper()
    for i, turn in enumerate(ordered):
        subj = str(turn.get("subject") or "").upper()
        if not subj.startswith(adm_prefix):
            continue
        n = _turn_number(turn)
        following_cp = None
        for later in ordered[i + 1 :]:
            if _turn_number(later) <= n:
                continue
            later_subj = str(later.get("subject") or "").upper()
            if later_subj.startswith(cp_prefix):
                following_cp = later
                break
        if following_cp is not None:
            pairs.append((turn, following_cp))
    return pairs


def _admission_posted_at(admission_turn: dict) -> datetime | None:
    try:
        meta = json.loads(str(admission_turn.get("body") or ""))
        raw = meta.get("posted_at")
        if not raw:
            return None
        parsed = datetime.fromisoformat(raw)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except (ValueError, TypeError):
        return None
