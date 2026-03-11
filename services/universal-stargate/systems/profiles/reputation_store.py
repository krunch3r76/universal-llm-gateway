from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from math import exp
from pathlib import Path
from threading import Lock
from time import monotonic
from typing import TYPE_CHECKING, Any

from src.scheduling.events.request import ModelSelectionHealthObservation

from .reputation_policy import DEFAULT_REPUTATION_POLICY, ReputationPolicy

if TYPE_CHECKING:
    from universal_event_bus import EventBus

logger = logging.getLogger(__name__)

_PERSISTENCE_PATH = Path.home() / ".gateway" / "reputation-store.json"
_SAVE_DEBOUNCE_SECONDS = 30.0

# Fields persisted across sessions (stable, slow-moving signals)
_PERSISTENT_FIELDS = (
    "toks_per_second_ewma",
    "quality_ewma",
    "request_ewma",
    "p50_latency_ms",
    "p90_latency_ms",
    "last_event_ts",
)


@dataclass(slots=True, kw_only=True)
class ReputationRecord:
    request_ewma: float = 0.0
    timeout_ewma: float = 0.0  # transient — not persisted
    error_ewma: float = 0.0  # transient — not persisted
    quality_ewma: float = 0.0
    toks_per_second_ewma: float | None = None
    p50_latency_ms: float | None = None
    p90_latency_ms: float | None = None
    last_event_ts: float = 0.0
    last_selected_ts: float = 0.0


class TaskModelReputationStore:
    """Persists and serves reputation scores for (task, model_id) pairs.

    Tracks request count, timeouts, errors, quality, latency, and tokens/sec
    per model. Observations update in-memory EWMAs; stable fields are written
    to disk after a debounced interval.
    """

    def __init__(
        self,
        policy: ReputationPolicy = DEFAULT_REPUTATION_POLICY,
        *,
        event_bus: EventBus | None = None,
        persistence_path: Path = _PERSISTENCE_PATH,
    ) -> None:
        self._lock: Lock = Lock()
        self._records: dict[tuple[str, str], ReputationRecord] = {}
        self._sticky_selected: dict[str, tuple[str, float]] = {}
        self._policy: ReputationPolicy = policy
        self._event_bus: EventBus | None = event_bus
        self._persistence_path: Path = persistence_path
        self._last_save_ts: float = 0.0
        self._save_pending: bool = False

    def get(self, task: str, model_id: str) -> ReputationRecord | None:
        with self._lock:
            return self._records.get((task, model_id))

    def get_all_for_task(self, task: str) -> dict[str, ReputationRecord]:
        """Return all records for a task, keyed by model_id."""
        with self._lock:
            return {
                model_id: rec
                for (t, model_id), rec in self._records.items()
                if t == task
            }

    def load(self, path: Path | None = None) -> None:
        """Load persisted stable fields from disk. Missing file is silently ignored."""
        target = path or self._persistence_path
        if not target.exists():
            return
        try:
            raw = json.loads(target.read_text())
        except (json.JSONDecodeError, OSError):
            logger.exception("Failed to load reputation store from %s", target)
            return
        except Exception:
            logger.exception(
                "Unexpected error loading reputation store from %s", target
            )
            return

        loaded = 0
        with self._lock:
            for key, fields in raw.items():
                # Key format: "task:model_id"
                if not (isinstance(key, str) and ":" in key):
                    continue
                task, model_id = key.split(":", 1)
                if not isinstance(fields, dict):
                    continue
                rec = ReputationRecord()
                for field in _PERSISTENT_FIELDS:
                    value = fields.get(field)
                    if value is not None:
                        setattr(rec, field, value)
                # Transient fields stay at zero — not loaded
                self._records[(task, model_id)] = rec
                loaded += 1

        logger.info("Loaded %d reputation records from %s", loaded, target)

    def _save_to_disk(self, data: dict[str, Any]) -> None:
        """Write serialized data to disk. Called from thread pool; does not acquire lock."""
        try:
            self._persistence_path.parent.mkdir(parents=True, exist_ok=True)
            self._persistence_path.write_text(json.dumps(data, indent=2))
        except OSError:
            logger.exception(
                "Failed to save reputation store to %s", self._persistence_path
            )
        except Exception:
            logger.exception(
                "Unexpected error saving reputation store to %s",
                self._persistence_path,
            )

    def _schedule_save(self) -> None:
        """Debounced async save. Observations arriving during a pending save
        are coalesced into the single scheduled write.
        Data is copied under lock; disk write runs in thread pool without lock.
        """
        now = monotonic()
        with self._lock:
            if now - self._last_save_ts >= _SAVE_DEBOUNCE_SECONDS:
                data_to_save = {
                    f"{task}:{model_id}": {
                        field: getattr(rec, field)
                        for field in _PERSISTENT_FIELDS
                        if getattr(rec, field) is not None
                    }
                    for (task, model_id), rec in self._records.items()
                }
                self._last_save_ts = now
                self._save_pending = False
                asyncio.create_task(
                    asyncio.to_thread(self._save_to_disk, data_to_save)
                )
            elif not self._save_pending:
                self._save_pending = True
                delay = _SAVE_DEBOUNCE_SECONDS - (now - self._last_save_ts)

                async def _deferred() -> None:
                    await asyncio.sleep(delay)
                    with self._lock:
                        data_to_save = {
                            f"{task}:{model_id}": {
                                field: getattr(rec, field)
                                for field in _PERSISTENT_FIELDS
                                if getattr(rec, field) is not None
                            }
                            for (task, model_id), rec in self._records.items()
                        }
                        self._last_save_ts = monotonic()
                        self._save_pending = False
                    await asyncio.to_thread(
                        self._save_to_disk, data_to_save
                    )

                asyncio.create_task(_deferred())

    def observe(
        self,
        *,
        task: str,
        model_id: str,
        latency_ms: float,
        outcome: str,
        quality_score: float | None,
        tokens_per_second: float | None = None,
    ) -> None:
        now = monotonic()
        with self._lock:
            rec = self._records.setdefault((task, model_id), ReputationRecord())
            dt = max(1e-9, now - rec.last_event_ts) if rec.last_event_ts > 0 else 1.0
            alpha = 1.0 - exp(-dt / self._policy.ewma_half_life_seconds)
            rec.request_ewma = (1 - alpha) * rec.request_ewma + alpha
            # timeout/error: shorter half-life → temporary weight, decay faster.
            alpha_fail = 1.0 - exp(-dt / self._policy.timeout_ewma_half_life_seconds)
            rec.timeout_ewma = (1 - alpha_fail) * rec.timeout_ewma + alpha_fail * (
                1.0 if outcome == "timeout" else 0.0
            )
            rec.error_ewma = (1 - alpha_fail) * rec.error_ewma + alpha_fail * (
                1.0 if outcome == "error" else 0.0
            )
            if quality_score is not None:
                rec.quality_ewma = (
                    1 - alpha
                ) * rec.quality_ewma + alpha * quality_score
            if tokens_per_second is not None and tokens_per_second > 0:
                if rec.toks_per_second_ewma is None:
                    rec.toks_per_second_ewma = tokens_per_second
                else:
                    tps = rec.toks_per_second_ewma
                    rec.toks_per_second_ewma = (
                        1 - alpha
                    ) * tps + alpha * tokens_per_second
            if rec.p50_latency_ms is None:
                rec.p50_latency_ms = latency_ms
            else:
                rec.p50_latency_ms = (
                    1 - alpha
                ) * rec.p50_latency_ms + alpha * latency_ms
            if rec.p90_latency_ms is None:
                rec.p90_latency_ms = latency_ms
            else:
                smoothed = (1 - alpha) * rec.p90_latency_ms + alpha * latency_ms
                rec.p90_latency_ms = max(latency_ms, smoothed)
            rec.last_event_ts = now

        self._schedule_save()

        if self._event_bus is not None:
            asyncio.create_task(
                self._event_bus.publish_async_nowait(
                    ModelSelectionHealthObservation(
                        task=task,
                        model_id=model_id,
                        outcome=outcome,
                        latency_ms=latency_ms,
                        quality_score=quality_score,
                        tokens_per_second=tokens_per_second,
                    )
                )
            )

    def remember_selection(self, sticky_key: str, model_id: str) -> None:
        with self._lock:
            self._sticky_selected[sticky_key] = (model_id, monotonic())

    def get_last_selection(self, sticky_key: str) -> tuple[str, float] | None:
        with self._lock:
            return self._sticky_selected.get(sticky_key)
