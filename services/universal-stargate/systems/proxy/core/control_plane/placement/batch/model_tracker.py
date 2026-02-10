"""
Cross-batch model load tracking.

Prevents resource conflicts when concurrent batches target the same model.
This is a proxy-domain component with ZERO knowledge of pipeline types.

Domain: Proxy
Invariant: ∀ model_id, ∃≤1 pending_load_operation

Lock-Free Justification (ADR-1):
- asyncio is single-threaded: no context switch without await
- All operations between check (get) and write (assignment) are synchronous
- No await points in critical section = atomically executed by event loop
- Per architecture_ws.mdc: lock_needed ⟺ (multi_step ∧ await_in_middle)
- Since await_in_middle = False, lock NOT needed
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from model_id import ModelId
from universal_logging import get_logger

logger = get_logger(__name__)


@dataclass(slots=True)
class LoadClaim:
    """Represents a model load claim."""

    gateway_name: str
    claimed_at: float = field(default_factory=time.time)
    batch_id: str | None = None


class BatchModelTracker:
    """
    Track models being loaded across concurrent batch operations.

    This is a proxy-domain component that tracks resource allocation
    without knowledge of pipeline types.

    Invariant: ∀ model_id, ∃≤1 pending_load_operation

    Lock-Free Design:
    - asyncio single-threaded: no preemption without await
    - All claim/release operations are synchronous (no await)
    - Between dict.get() and dict assignment, no await = atomic execution
    - Safe because Python's GIL + asyncio event loop semantics

    Usage:
        tracker = BatchModelTracker(stale_timeout=300)

        # Attempt to claim model load
        if tracker.claim_model_load(model_id, gateway_name, batch_id):
            # Claimed successfully - proceed with load
            try:
                await load_model(...)
            finally:
                tracker.release_model_load(model_id)
        else:
            # Model already loading - join existing gateway
            gateway = tracker.get_pending_gateway(model_id)
    """

    def __init__(self, stale_claim_timeout: float = 300.0) -> None:
        """
        Initialize tracker.

        Args:
            stale_claim_timeout: Seconds before auto-releasing stale claims
        """
        self._pending_loads: dict[str, LoadClaim] = {}
        self._stale_timeout = stale_claim_timeout

    def claim_model_load(
        self,
        model_id: ModelId,
        gateway_name: str,
        batch_id: str | None = None,
    ) -> bool:
        """
        Attempt to claim exclusive load for model on gateway.

        Returns False if model already being loaded elsewhere.
        Returns True if claim granted (caller now owns the load operation).

        Lock-free: No await between read and write, so atomically
        executed in asyncio's single-threaded event loop.

        Args:
            model_id: Model to claim
            gateway_name: Gateway that will load the model
            batch_id: Optional batch ID for debugging

        Returns:
            True if claim granted, False if model already loading
        """
        # Cleanup stale claims opportunistically
        self._cleanup_stale_claims()

        key = model_id.routing_key
        existing = self._pending_loads.get(key)

        if existing is not None:
            if existing.gateway_name == gateway_name:
                # Same gateway claiming again (idempotent)
                logger.debug(
                    f"Model {model_id} already claimed by {gateway_name} "
                    f"(batch: {existing.batch_id})"
                )
                return True

            # Different gateway - reject claim
            logger.debug(
                f"Model {model_id} already loading on {existing.gateway_name} "
                f"(batch: {existing.batch_id}), rejecting claim for {gateway_name}"
            )
            return False

        # Grant claim - atomic with check above (no await between)
        self._pending_loads[key] = LoadClaim(
            gateway_name=gateway_name,
            batch_id=batch_id,
        )
        logger.debug(
            f"Granted load claim for {model_id} on {gateway_name} (batch: {batch_id})"
        )
        return True

    def release_model_load(self, model_id: ModelId) -> bool:
        """
        Release load claim after completion or failure.

        Single atomic dict.pop() - no lock needed.

        Args:
            model_id: Model to release

        Returns:
            True if claim was released, False if no claim existed
        """
        key = model_id.routing_key
        claim = self._pending_loads.pop(key, None)
        if claim:
            logger.debug(
                f"Released load claim for {model_id} on {claim.gateway_name} "
                f"(batch: {claim.batch_id})"
            )
            return True
        return False

    def get_pending_gateway(self, model_id: ModelId) -> str | None:
        """
        Get gateway where model is being loaded (if any).

        Args:
            model_id: Model to check

        Returns:
            Gateway name if model is being loaded, None otherwise
        """
        key = model_id.routing_key
        claim = self._pending_loads.get(key)
        return claim.gateway_name if claim else None

    def get_pending_claim(self, model_id: ModelId) -> LoadClaim | None:
        """
        Get full claim info for model (if any).

        Args:
            model_id: Model to check

        Returns:
            LoadClaim if model is being loaded, None otherwise
        """
        key = model_id.routing_key
        return self._pending_loads.get(key)

    def get_all_pending(self) -> dict[str, str]:
        """
        Get all pending model loads.

        Returns:
            Dict mapping model_id → gateway_name for all pending loads
        """
        return {
            model_id: claim.gateway_name
            for model_id, claim in self._pending_loads.items()
        }

    @property
    def pending_count(self) -> int:
        """Get count of pending model loads."""
        return len(self._pending_loads)

    def _cleanup_stale_claims(self) -> None:
        """
        Remove stale claims.

        Stale claims can occur if a batch fails without releasing.
        All sync operations - no lock needed.
        """
        now = time.time()
        stale_models = [
            model_id
            for model_id, claim in self._pending_loads.items()
            if (now - claim.claimed_at) > self._stale_timeout
        ]

        for model_id in stale_models:
            claim = self._pending_loads.pop(model_id)
            logger.warning(
                f"Auto-released stale claim for {model_id} on {claim.gateway_name} "
                f"(batch: {claim.batch_id}, age: {now - claim.claimed_at:.1f}s)"
            )
