"""Manage-hosted charter-runner tick.

Periodic supervisor that watches enrolled standing roots and admits one window
per eligible root. Default substrate is unattended Grok 4.5
(``cursor/grok-4.5``, effort=high, fast=true) via generate dispatch.
Opt-in attended handoff (``CHARTER_ADMISSION_MODE=handoff``) POSTs
``/api/v1/team/handoff`` with ``role=cursor-consult`` for IDE observation.
``CHARTER_ADMISSION_MODE=autonomous`` selects the background-lead packet and
auto-arms hard stall at ``DEFAULT_AUTONOMOUS_STALE_S`` (3600s) unless
``CHARTER_UNATTENDED_STALE_S`` overrides (incl. ``0`` = force OFF).
Consult-mode windows additionally recover at ``DEFAULT_CONSULT_STALE_S`` (900s)
via ``consult_stall`` (R-ADMIT on root advances; else re-queue CONSULT_PENDING)
so a hung CDP/consult seat cannot pin the root for a full hour (a:26131).
CHECKPOINT on the charter root clears in-flight.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from universal_logging import get_logger

from scripts.model_manager import observation_event as events

# Stale-manage heal: charter_reload historically omitted observation_event, so a
# manage process started before G3 emitters lacks root_skipped and dies on first
# skip. Reload in-place before state_close binds ``events`` (module object shared).
if not hasattr(events, "emit_manage_charter_tick_root_skipped"):
    importlib.reload(events)

from scripts.model_manager.ui.controller.service_config import build_mcp_env
from scripts.model_manager.ui.controller.shutdown_gate import ManageShutdownGate
from scripts.model_manager.ui.model.service_state import ServiceState, ServiceStatus

from . import admit, bus_client, window_log
from . import consult_stall as _consult_stall_mod
from . import schema_skip_heal as _schema_skip_heal_mod
from . import self_heal as _self_heal_mod
from . import state_close as _state_close_mod
from .caps import CapStore
from .checkpoint_admit_gate import SCHEMA_REASONS
from .dispatch_client import AdmissionMode
from .eligibility import (
    Decision,
    evaluate_root,
    live_wip_for_window,
    next_window_index,
)
from .giw_live_hold import build_tick_env_snapshot
from .harvest import completed_windows, harvest_completed_windows
from .tick_interval import tick_interval_from_env as _tick_interval_from_env

# One-shot at tick_loop import: long-lived manage may hold pre-census modules
# in sys.modules (r_corpus_sha / self_heal / state_close) until ./manage process
# recycle picks up app.py reload-driver + reload.py census.
_self_heal_mod = importlib.reload(_self_heal_mod)
_schema_skip_heal_mod = importlib.reload(_schema_skip_heal_mod)
_consult_stall_mod = importlib.reload(_consult_stall_mod)
_state_close_mod = importlib.reload(_state_close_mod)
MAX_STATE_CLOSES_PER_TICK = _state_close_mod.MAX_STATE_CLOSES_PER_TICK
emit_skip_and_maybe_state_close = _state_close_mod.emit_skip_and_maybe_state_close
maybe_state_close_root = _state_close_mod.maybe_state_close_root
DEFAULT_CONSULT_STALE_S = _consult_stall_mod.DEFAULT_CONSULT_STALE_S

logger = get_logger(__name__)


_ACTIVITY = "charter_tick"
# Soft remind only — attended handoffs may sit until the operator opens IDE.
_WAITING_OPEN_REMIND_S = 900.0
# Hard stall (stale_window): autonomous default-on; other modes env-opt-in.
_ENV_UNATTENDED_STALE_S = "CHARTER_UNATTENDED_STALE_S"
_ENV_ADMISSION_MODE = "CHARTER_ADMISSION_MODE"
_ENV_KEYS = ("AGENT_BUS_TOKEN",)
# Margin over CURSOR_SDK_TIMEOUT (1800) so stale cannot race worker_failed (A1).
DEFAULT_AUTONOMOUS_STALE_S = 3600.0

# Re-export for tests that import via tick_loop.
_completed_windows = completed_windows


async def _worker_terminal_or_absent(worker_thread: str | None) -> bool:
    """True when the orphan-intent worker is gone or finished (heal-safe)."""
    if not worker_thread:
        return True
    try:
        failure = await bus_client.worker_failure_reason(worker_thread)
        if failure is not None:
            return True
        detail = await bus_client.fetch_thread(worker_thread)
        status = str(detail.get("status") or "").lower()
        if not status and isinstance(detail.get("thread"), dict):
            status = str((detail.get("thread") or {}).get("status") or "").lower()
        return status == "closed"
    except Exception:  # noqa: BLE001 — unreachable worker ⇒ absent
        return True


async def maybe_heal_admit_intent_orphan(
    root_id: str,
    turns: list[dict],
    caps: CapStore,
) -> bool:
    """Clear orphan admit-intent when WIP is gone and the worker is terminal/absent.

    Stopped roots keep intentional intents (a:26167 5xx / pointer-fail keep-intent) —
    heal must not clear those; charter_reload clears stopped before orphans can heal.
    """
    window_index = next_window_index(turns)
    if not caps.has_admit_intent(root_id, window_index):
        return False
    allowed, reason = caps.check(root_id)
    if not allowed and reason and reason.startswith("stopped:"):
        return False
    if live_wip_for_window(turns, window_index):
        return False
    worker = caps.resolve_orphan_worker_thread(root_id, window_index)
    if not await _worker_terminal_or_absent(worker):
        return False
    caps.clear_admit_intent(root_id, window_index)
    await events.emit_manage_charter_tick_intent_healed(
        root=root_id,
        window_index=window_index,
        worker_thread=worker or "",
    )
    return True


def _env_unattended_stale_raw() -> float | None:
    """Parse CHARTER_UNATTENDED_STALE_S; None means unset or malformed (A5).

    Empty/whitespace → None (unset). Non-float → None (malformed→unset).
    Negatives clamp to 0.0 (force-OFF). Explicit 0.0 is force-OFF, not unset.
    """
    raw = os.environ.get(_ENV_UNATTENDED_STALE_S, "").strip()
    if not raw:
        return None  # unset
    try:
        return max(0.0, float(raw))
    except ValueError:
        return None  # malformed → treat as unset (A5)


def _unattended_stale_s_from_env() -> float:
    """Legacy shim: unset/malformed → 0.0; else parsed seconds. Delegates to raw."""
    val = _env_unattended_stale_raw()
    return 0.0 if val is None else val


def _effective_unattended_stale_s(*, constructor_override: float | None) -> float:
    """Resolve hard-stall seconds: constructor → env → autonomous default → 0.

    Under admission_mode=autonomous with env unset, returns DEFAULT_AUTONOMOUS_STALE_S
    (3600). Explicit env (including 0) always wins. Constructor override freezes for tests.
    """
    if constructor_override is not None:
        return max(0.0, float(constructor_override))
    env_val = _env_unattended_stale_raw()
    if env_val is not None:
        return env_val
    if _admission_mode() == "autonomous":
        return DEFAULT_AUTONOMOUS_STALE_S
    return 0.0


def _admission_mode_path() -> Path:
    """Durable arming file — overrides env so tick can re-arm without process restart."""
    return Path.home() / ".local" / "share" / "charter-runner" / "admission_mode"


def _resolve_admission_token(raw: str) -> AdmissionMode | None:
    token = raw.strip().lower()
    if not token or token == "generate":
        return "generate"
    if token == "handoff":
        return "handoff"
    if token == "autonomous":
        return "autonomous"
    if token == "consult":
        return "consult"
    return None


def _admission_mode() -> AdmissionMode:
    """Resolve admission mode: durable file (if present) then ``CHARTER_ADMISSION_MODE``.

    ``autonomous`` selects the background-lead packet (full path-sim arc via
    satellite R-admit + capped revise loop). ``generate`` (default) and ``handoff``
    are unchanged. File path: ``~/.local/share/charter-runner/admission_mode``.
    """
    path = _admission_mode_path()
    if path.is_file():
        try:
            file_raw = path.read_text(encoding="utf-8").splitlines()[0]
        except (OSError, IndexError):
            file_raw = ""
        resolved = _resolve_admission_token(file_raw)
        if resolved is not None:
            return resolved
        logger.warning(
            "charter-runner: unknown admission_mode file %r — falling through to env",
            file_raw,
        )
    raw = os.environ.get(_ENV_ADMISSION_MODE, "").strip().lower()
    resolved = _resolve_admission_token(raw)
    if resolved is not None:
        return resolved
    logger.warning(
        "charter-runner: unknown CHARTER_ADMISSION_MODE=%r — treating as generate",
        raw,
    )
    return "generate"


def ensure_charter_tick_env(workspace_root: Path) -> None:
    """Overlay bus auth into the manage process env (token is not ambient)."""
    merged = build_mcp_env(workspace_root)
    for key in _ENV_KEYS:
        if merged.get(key):
            os.environ[key] = merged[key]


class CharterRunnerTickLoop:
    """Async supervisor: scan enrolled roots + admit generate or handoff windows."""

    def __init__(
        self,
        *,
        service_state: ServiceState,
        shutdown_gate: ManageShutdownGate,
        workspace_root: Path | None = None,
        tick_interval_s: float | None = None,
        caps: CapStore | None = None,
        on_admit: Callable[[str], None] | None = None,
        unattended_stale_s: float | None = None,
    ) -> None:
        self._service_state = service_state
        self._shutdown_gate = shutdown_gate
        self._workspace_root = workspace_root
        # None / omitted → env (CHARTER_TICK_INTERVAL_S); explicit float wins (tests).
        if tick_interval_s is None:
            self._tick_interval_s = _tick_interval_from_env()
        else:
            self._tick_interval_s = float(tick_interval_s)
        self._caps = caps or CapStore()
        self._on_admit = on_admit
        # None → resolve at handle time (env / autonomous default); float freezes tests.
        self._unattended_stale_override = unattended_stale_s
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
        env_snapshot = await build_tick_env_snapshot()
        admitted = 0
        skipped_by_reason: dict[str, int] = {}
        state_closes_this_tick = 0
        for thread in roots:
            root_id = str(thread.get("id") or "")
            if not root_id:
                continue
            turns = await bus_client.fetch_turns(root_id)
            await harvest_completed_windows(root_id, turns)
            await maybe_heal_admit_intent_orphan(root_id, turns, self._caps)
            decision = evaluate_root(
                root_id, turns, self._caps, env_snapshot=env_snapshot
            )
            if decision.eligible:
                try:
                    if await self._admit_window(decision, turns):
                        admitted += 1
                except Exception:
                    logger.exception(
                        "charter-runner admit failed root=%s — continuing scan",
                        root_id,
                    )
                continue
            # Every ineligible Decision emits root_skipped (silent-starve fix).
            state_closes_this_tick = await emit_skip_and_maybe_state_close(
                decision,
                state_closes_this_tick=state_closes_this_tick,
                skipped_by_reason=skipped_by_reason,
                max_state_closes=MAX_STATE_CLOSES_PER_TICK,
                caps=self._caps,
            )
            if decision.reason == "window_in_flight":
                if await self._recover_worker_failure(decision):
                    continue
                if await self._try_consult_stall(decision, turns):
                    continue
                if await self._try_self_heal(decision, turns):
                    continue
                state_closes_this_tick = await self._handle_waiting_open(
                    decision, turns, state_closes_this_tick=state_closes_this_tick
                )
            elif decision.reason in SCHEMA_REASONS:
                await _schema_skip_heal_mod.try_self_heal_schema_skip(
                    decision, caps=self._caps
                )
        await events.emit_manage_charter_tick_scanned(
            roots=len(roots),
            admitted=admitted,
            skipped_by_reason=skipped_by_reason,
        )
        try:
            from . import conveyor as _conveyor_mod

            await _conveyor_mod.sweep_stale_enrollments()
        except Exception:  # noqa: BLE001 — stale sweep must not abort tick
            logger.exception("charter-runner conveyor stale sweep failed")

    async def _recover_worker_failure(self, decision: Decision) -> bool:
        """A-R3-1: failed/timeout/closed worker while in-flight → stop root."""
        adm = decision.admission_turn or {}
        meta = window_log.parse_admission_meta(str(adm.get("body") or ""))
        worker_thread = str(meta.get("worker_thread") or "")
        if not worker_thread:
            return False
        try:
            reason = await bus_client.worker_failure_reason(worker_thread)
        except Exception:  # noqa: BLE001 — leave in-flight; soft-remind path
            logger.exception(
                "charter-runner worker-failure probe failed for %s", worker_thread
            )
            return False
        if reason is None:
            return False
        self._caps.mark_failed(decision.root_id, "worker_failed")
        await events.emit_manage_charter_tick_window_failed(
            root=decision.root_id, reason="worker_failed"
        )
        return True

    async def _admit_window(self, decision: Decision, turns: list[dict]) -> bool:
        if self._workspace_root is None:
            raise RuntimeError(
                "charter-runner requires workspace_root for handoff packets"
            )
        admitted = await admit.admit_window(
            decision=decision,
            turns=turns,
            caps=self._caps,
            workspace_root=self._workspace_root,
            on_admit=self._on_admit,
        )
        if admitted:
            self._reminded.discard(decision.root_id)
        return admitted

    async def _try_self_heal(self, decision: Decision, turns: list[dict]) -> bool:
        """Autonomous: complete/partial without root terminal → machine heal."""
        adm = decision.admission_turn or {}
        posted_at = _admission_posted_at(adm)
        if posted_at is None:
            return False
        age = (datetime.now(UTC) - posted_at).total_seconds()
        return await _self_heal_mod.try_self_heal_incomplete_window(
            decision,
            root_turns=turns,
            caps=self._caps,
            age_s=age,
            admission_mode=_admission_mode(),
        )

    async def _try_consult_stall(self, decision: Decision, turns: list[dict]) -> bool:
        """Consult-mode: hung WIP past DEFAULT_CONSULT_STALE_S → recover (a:26131)."""
        adm = decision.admission_turn or {}
        posted_at = _admission_posted_at(adm)
        if posted_at is None:
            return False
        age = (datetime.now(UTC) - posted_at).total_seconds()
        return await _consult_stall_mod.try_recover_consult_stall(
            decision,
            root_turns=turns,
            caps=self._caps,
            age_s=age,
            admission_mode=_admission_mode(),
        )

    async def _handle_waiting_open(
        self,
        decision: Decision,
        turns: list[dict],
        *,
        state_closes_this_tick: int = 0,
    ) -> int:
        """Soft remind by default; unattended hard-fail past effective stale threshold.

        Broad ``stopped:*`` early-out (A2): once a root is already stopped for any
        reason, do not re-emit ``window_failed`` / overwrite ``stopped_reason`` —
        ``window_in_flight`` short-circuits before caps in ``evaluate_root``, so
        this guard is the only barrier against stale restop. Exact
        ``stopped:stale_window`` state-close lives in ``emit_skip_and_maybe_state_close``
        / transition close below — not in this early-out.

        Prefer self-heal over ``stale_window`` when the closeout-grace window has
        elapsed (may re-probe after an earlier grace-blocked attempt). Returns the
        updated A4 ``state_closes_this_tick`` counter for ``_tick_once`` to consume.
        """
        allowed, cap_reason = self._caps.check(decision.root_id)
        if not allowed and (cap_reason or "").startswith("stopped:"):
            return state_closes_this_tick
        adm = decision.admission_turn or {}
        posted_at = _admission_posted_at(adm)
        if posted_at is None:
            return state_closes_this_tick
        age = (datetime.now(UTC) - posted_at).total_seconds()
        stale_s = _effective_unattended_stale_s(
            constructor_override=self._unattended_stale_override
        )
        if stale_s > 0 and age >= stale_s:
            if await self._try_self_heal(decision, turns):
                return state_closes_this_tick
            if await self._try_consult_stall(decision, turns):
                return state_closes_this_tick
            if await _self_heal_mod.closeout_within_grace(decision):
                return state_closes_this_tick
            self._caps.mark_failed(decision.root_id, "stale_window")
            await events.emit_manage_charter_tick_window_failed(
                root=decision.root_id, reason="stale_window"
            )
            return await maybe_state_close_root(
                decision,
                reason="stale_window",
                state_closes_this_tick=state_closes_this_tick,
                max_state_closes=MAX_STATE_CLOSES_PER_TICK,
            )
        if age < _WAITING_OPEN_REMIND_S:
            return state_closes_this_tick
        if decision.root_id in self._reminded:
            return state_closes_this_tick
        self._reminded.add(decision.root_id)
        await events.emit_manage_charter_tick_waiting_open(
            root=decision.root_id, age_s=int(age)
        )
        return state_closes_this_tick


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
