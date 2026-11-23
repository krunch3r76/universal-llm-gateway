"""Evidence-kept loading reservations for master FederatedGatewayManager.

Authority: lease table keyed (gateway_id, ModelId); ``loading_models`` on
FederatedGateway is the routing projection derived from active leases.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from math import ceil

from model_id import ModelId

DEFAULT_LOAD_ACK_GRACE_S = 15


class LeaseOrigin(StrEnum):
    """How the master first recorded the loading mark."""

    OPTIMISTIC = "optimistic"
    ORCHESTRATOR = "orchestrator"
    TELEMETRY = "telemetry"


class ExpireReason(StrEnum):
    UNACKED = "unacked"
    IDLE = "idle"


@dataclass(frozen=True, slots=True)
class LoadingLease:
    gateway_id: str
    model_id: ModelId
    marked_at: float
    acked_at: float | None
    last_evidence_at: float
    origin: LeaseOrigin


@dataclass(frozen=True, slots=True)
class ExpiredLease:
    gateway_id: str
    model_id: ModelId
    reason: ExpireReason
    age_s: float
    origin: LeaseOrigin


class LoadingLeaseTable:
    """In-memory lease journal; expire-on-read via :meth:`expire_gateway`."""

    def __init__(
        self,
        *,
        load_ack_grace_s: int = DEFAULT_LOAD_ACK_GRACE_S,
        load_idle_budget: int = 60,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._load_ack_grace_s = load_ack_grace_s
        self._load_idle_budget = load_idle_budget
        self._clock = clock or time.monotonic
        self._leases: dict[tuple[str, ModelId], LoadingLease] = {}

    @property
    def load_ack_grace_s(self) -> int:
        return self._load_ack_grace_s

    @property
    def load_idle_budget(self) -> int:
        return self._load_idle_budget

    @staticmethod
    def effective_unacked_grace_s(
        *,
        floor_s: int,
        is_http_polling: bool,
        telemetry_poll_interval_ms: int,
    ) -> float:
        """Per-gateway unacked grace: floor for WS; max(floor, 2×poll) for HTTP."""
        if not is_http_polling:
            return float(floor_s)
        poll_grace = ceil(2 * telemetry_poll_interval_ms / 1000)
        return float(max(floor_s, poll_grace))

    def mark(
        self,
        gateway_id: str,
        model_id: ModelId,
        origin: LeaseOrigin,
    ) -> None:
        now = self._clock()
        key = (gateway_id, model_id)
        self._leases[key] = LoadingLease(
            gateway_id=gateway_id,
            model_id=model_id,
            marked_at=now,
            acked_at=None,
            last_evidence_at=now,
            origin=origin,
        )

    def ack(self, gateway_id: str, model_id: ModelId) -> None:
        now = self._clock()
        key = (gateway_id, model_id)
        lease = self._leases.get(key)
        if lease is None:
            self._leases[key] = LoadingLease(
                gateway_id=gateway_id,
                model_id=model_id,
                marked_at=now,
                acked_at=now,
                last_evidence_at=now,
                origin=LeaseOrigin.TELEMETRY,
            )
            return
        self._leases[key] = LoadingLease(
            gateway_id=lease.gateway_id,
            model_id=lease.model_id,
            marked_at=lease.marked_at,
            acked_at=now,
            last_evidence_at=now,
            origin=lease.origin,
        )

    def touch_progress(self, gateway_id: str, model_id: ModelId) -> None:
        now = self._clock()
        key = (gateway_id, model_id)
        lease = self._leases.get(key)
        if lease is None:
            return
        self._leases[key] = LoadingLease(
            gateway_id=lease.gateway_id,
            model_id=lease.model_id,
            marked_at=lease.marked_at,
            acked_at=lease.acked_at if lease.acked_at is not None else now,
            last_evidence_at=now,
            origin=lease.origin,
        )

    def release(self, gateway_id: str, model_id: ModelId) -> None:
        self._leases.pop((gateway_id, model_id), None)

    def release_loaded(self, gateway_id: str, loaded: frozenset[ModelId]) -> None:
        if not loaded:
            return
        for key in list(self._leases):
            gid, mid = key
            if gid == gateway_id and mid in loaded:
                del self._leases[key]

    def clear_gateway(self, gateway_id: str) -> None:
        for key in [k for k in self._leases if k[0] == gateway_id]:
            del self._leases[key]

    def loading_models_for(self, gateway_id: str) -> frozenset[ModelId]:
        return frozenset(mid for (gid, mid) in self._leases if gid == gateway_id)

    def expire_gateway(
        self,
        gateway_id: str,
        *,
        unacked_grace_s: float,
        idle_budget_s: int | None = None,
    ) -> list[ExpiredLease]:
        """Drop expired leases for one gateway; caller emits events once each."""
        idle_budget = (
            idle_budget_s if idle_budget_s is not None else self._load_idle_budget
        )
        now = self._clock()
        expired: list[ExpiredLease] = []
        for key in list(self._leases):
            gid, model_id = key
            if gid != gateway_id:
                continue
            lease = self._leases[key]
            if lease.acked_at is None:
                age = now - lease.marked_at
                if age > unacked_grace_s:
                    expired.append(
                        ExpiredLease(
                            gateway_id=gateway_id,
                            model_id=model_id,
                            reason=ExpireReason.UNACKED,
                            age_s=age,
                            origin=lease.origin,
                        )
                    )
                    del self._leases[key]
                continue
            silence = now - lease.last_evidence_at
            if silence > idle_budget:
                expired.append(
                    ExpiredLease(
                        gateway_id=gateway_id,
                        model_id=model_id,
                        reason=ExpireReason.IDLE,
                        age_s=silence,
                        origin=lease.origin,
                    )
                )
                del self._leases[key]
        return expired
