"""Asynchronous CDP attachment occupancy projection for restart safety.

The execution store owns stream admission; this module owns only the observed
browser-attachment side of the restart-drain decision.  A single background
actor performs the blocking Chrome census in a worker thread, records the last
observation in memory, and exposes it synchronously to request handlers.
Observation events are advisory telemetry and never become the state authority.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

from claude_bundles import cdp_registry_events
from universal_logging import get_logger

logger = get_logger("cdp-ask.occupancy-projection")

_DEFAULT_INTERVAL_S = 30.0
_DEFAULT_FRESHNESS_TTL_S = 90.0
_OBSERVATION_SOURCE = "cdp_orphans.probe_live_ports"
_REGISTRY_BOOTSTRAP_SOURCE = "cse-session-registry"

ProbeLivePorts = Callable[[], list[Any]]
CapacityProbe = Callable[[], int]


def _positive_env(name: str, default: float) -> float:
    """Read a positive floating-point tuning value without weakening defaults."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def occupancy_interval_s() -> float:
    """Return the configured background census interval for this satellite in seconds."""
    return _positive_env("CDP_OCCUPANCY_INTERVAL_S", _DEFAULT_INTERVAL_S)


def occupancy_freshness_ttl_s() -> float:
    """Return the maximum age at which a census may authorize an idle drain."""
    return _positive_env(
        "CDP_OCCUPANCY_FRESHNESS_TTL_S",
        _DEFAULT_FRESHNESS_TTL_S,
    )


def _default_probe() -> list[Any]:
    """Probe registered CDP ports; imported lazily for hermetic callers."""
    from claude_bundles import cdp_orphans

    return cdp_orphans.probe_live_ports()


def _default_capacity_probe() -> int:
    """Read recorded registry-host capacity without probing Chrome pages."""
    from claude_bundles import cdp_registry

    return cdp_registry.count_capacity_lanes()


def _default_bootstrap_probe() -> int:
    """Fold durable session transitions before reading registry capacity."""
    from claude_bundles import cse_session_fold

    cse_session_fold.fold_pending_transitions()
    return _default_capacity_probe()


@dataclass(frozen=True, slots=True)
class OccupancyObservation:
    """Last sampled attachment state and its provenance metadata."""

    live_cse_count: int | None = None
    registry_capacity_count: int | None = None
    observed_at: float | None = None
    error: str | None = None
    source: str = _OBSERVATION_SOURCE


class CdpOccupancyProjection:
    """Single-flight async sensor and in-process drain-state projection.

    ``start`` launches one worker task that performs an immediate census and
    then waits for either a registry-change wakeup or the bounded recovery
    interval.  ``snapshot`` never performs I/O, and stale or unobserved
    liveness is intentionally fail-closed through ``safe_busy``.
    """

    def __init__(
        self,
        *,
        interval_s: float | None = None,
        freshness_ttl_s: float | None = None,
        probe: ProbeLivePorts | None = None,
        capacity_probe: CapacityProbe | None = None,
        bootstrap_probe: CapacityProbe | None = None,
    ) -> None:
        self._interval_s = (
            occupancy_interval_s() if interval_s is None else float(interval_s)
        )
        self._freshness_ttl_s = (
            occupancy_freshness_ttl_s()
            if freshness_ttl_s is None
            else float(freshness_ttl_s)
        )
        if self._interval_s <= 0 or self._freshness_ttl_s <= 0:
            raise ValueError("occupancy interval and freshness TTL must be positive")
        self._probe = probe or _default_probe
        self._capacity_probe = capacity_probe or _default_capacity_probe
        self._bootstrap_probe = bootstrap_probe or (
            _default_bootstrap_probe
            if capacity_probe is None
            else self._capacity_probe
        )
        self._observation = OccupancyObservation()
        self._refresh_lock = asyncio.Lock()
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._unsubscribe: Callable[[], None] | None = None
        self._last_event_key: tuple[str, int | None, int | None] | None = None

    @property
    def running(self) -> bool:
        """Return whether the background sensor task is currently scheduled."""
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        """Bootstrap recorded capacity and start the single-flight sensor."""
        if self.running:
            return
        self._loop = asyncio.get_running_loop()
        self._unsubscribe = cdp_registry_events.subscribe_registry_transitions(
            self.request_refresh
        )
        await self._bootstrap_registry_capacity()
        self._task = asyncio.create_task(self._run(), name="cdp-occupancy")

    async def stop(self) -> None:
        """Cancel the sensor task without performing a synchronous census."""
        task = self._task
        self._task = None
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._loop = None

    def request_refresh(self) -> None:
        """Wake the sensor after a registry transition without starting a scan."""
        if self._loop is not None and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._wake.set)
        else:
            self._wake.set()

    def snapshot(self, *, now: float | None = None) -> dict[str, Any]:
        """Return the cached projection without filesystem, socket, or HTTP I/O."""
        current = time.time() if now is None else now
        freshness = self._freshness(current)
        observed_at = self._observation.observed_at
        age = None if observed_at is None else max(0.0, current - observed_at)
        return {
            "live_cse_count": self._observation.live_cse_count,
            "registry_capacity_count": self._observation.registry_capacity_count,
            "observed_at": observed_at,
            "observation_age_s": age,
            "freshness": freshness,
            "error": self._observation.error,
            "source": self._observation.source,
        }

    def safe_busy(self, running_count: int, *, now: float | None = None) -> bool:
        """Return drain busy, refusing to claim idle on stale or unknown data."""
        observation = self.snapshot(now=now)
        if observation["freshness"] != "fresh":
            return True
        return running_count > 0 or int(observation["live_cse_count"] or 0) > 0

    def record_observation(
        self,
        live_cse_count: int,
        registry_capacity_count: int,
        *,
        observed_at: float | None = None,
    ) -> None:
        """Record a sampled census for tests or an owning sensor actor.

        This method only mutates the in-process projection.  The asynchronous
        ``refresh`` path emits the corresponding observation event off the
        request loop, keeping synchronous callers free of socket I/O.
        """
        if live_cse_count < 0 or registry_capacity_count < 0:
            raise ValueError("occupancy counts must be non-negative")
        self._observation = OccupancyObservation(
            live_cse_count=live_cse_count,
            registry_capacity_count=registry_capacity_count,
            observed_at=time.time() if observed_at is None else observed_at,
            error=None,
            source=_OBSERVATION_SOURCE,
        )

    async def refresh(self) -> dict[str, Any]:
        """Run one serialized census and publish only changed telemetry."""
        async with self._refresh_lock:
            try:
                ports = await asyncio.to_thread(self._probe)
                capacity = await asyncio.to_thread(self._capacity_probe)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — projection must survive sensor faults
                event = self._record_failure(exc)
            else:
                live_cse_count = sum(
                    1 for port in ports if bool(getattr(port, "has_live_cse", False))
                )
                event = self._record_success(live_cse_count, int(capacity))
        if event is not None:
            await asyncio.to_thread(cdp_registry_events.emit, event)
        return self.snapshot()

    async def _bootstrap_registry_capacity(self) -> None:
        try:
            capacity = await asyncio.to_thread(self._bootstrap_probe)
        except Exception as exc:  # noqa: BLE001 — unknown remains fail-closed
            logger.warning("CDP occupancy registry bootstrap failed: %s", exc)
            return
        self._observation = replace(
            self._observation,
            registry_capacity_count=int(capacity),
            source=_REGISTRY_BOOTSTRAP_SOURCE,
        )

    async def _run(self) -> None:
        while True:
            self._wake.clear()
            try:
                await self.refresh()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — standing sensor must not die
                logger.exception("CDP occupancy refresh failed")
            if self._wake.is_set():
                continue
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self._interval_s)
            except TimeoutError:
                continue

    def _freshness(self, now: float) -> str:
        observed_at = self._observation.observed_at
        if observed_at is None:
            return "unobserved"
        return (
            "fresh"
            if now - observed_at <= self._freshness_ttl_s
            else "stale"
        )

    def _record_success(self, live_cse_count: int, capacity: int) -> Any:
        now = time.time()
        previous_status = self._freshness(now)
        self._observation = OccupancyObservation(
            live_cse_count=live_cse_count,
            registry_capacity_count=capacity,
            observed_at=now,
            error=None,
            source=_OBSERVATION_SOURCE,
        )
        return self._event_if_changed(previous_status)

    def _record_failure(self, exc: Exception) -> Any:
        now = time.time()
        previous_status = self._freshness(now)
        self._observation = replace(self._observation, error=str(exc))
        return self._event_if_changed(previous_status)

    def _event_if_changed(self, previous_status: str) -> Any:
        status = self._freshness(time.time())
        key = (
            status,
            self._observation.live_cse_count,
            self._observation.registry_capacity_count,
        )
        if key == self._last_event_key:
            return None
        self._last_event_key = key
        return cdp_registry_events.cdp_occupancy_updated(
            live_cse_count=self._observation.live_cse_count,
            registry_capacity_count=self._observation.registry_capacity_count,
            freshness=status,
            previous_freshness=previous_status,
            error=self._observation.error,
        )
