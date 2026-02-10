"""Slot management helpers for federation inference.

Provides slot reservation/release for Edge capacity enforcement.
Invariant: ∀ reserved_slot: released on request completion ∨ error

Capacity keying: (gateway_id, endpoint_category, compute_type)
"""

from model_id import ModelId
from universal_logging import get_logger

logger = get_logger(__name__)


class SlotContext:
    """Context for slot reservation lifecycle.

    Usage:
        slot_ctx = SlotContext(gateway_id, model_str, request_id,
                               endpoint_category, compute_type)
        if gateway_id and not slot_ctx.try_reserve(max_concurrent_requests):
            raise HTTPException(503, detail=...)  # Caller raises
        try:
            ... do work ...
        finally:
            slot_ctx.release()
    """

    def __init__(
        self,
        gateway_id: str | None,
        model_str: str,
        request_id: str,
        endpoint_category: str,
        compute_type: str,
    ):
        self._gateway_id = gateway_id
        self._model_str = model_str
        self._request_id = request_id
        self._endpoint_category = endpoint_category
        self._compute_type = compute_type
        self._reserved = False
        self._routing_key: str | None = None

    @property
    def reserved(self) -> bool:
        return self._reserved

    def try_reserve(self, max_concurrent_requests: int = 1) -> bool:
        """Attempt to reserve a slot.

        Args:
            max_concurrent_requests: Maximum in-flight requests for this capacity key

        Returns:
            True if reserved or no reservation needed, False if at capacity.

        Note:
            Caller is responsible for raising HTTPException on False.
        """
        if not self._gateway_id:
            return True  # No gateway_id = no slot reservation needed

        from src.core.gateway_tracker import gateway_tracker

        try:
            model_id = ModelId.parse(self._model_str)
            self._routing_key = model_id.routing_key
        except ValueError as e:
            logger.error(
                f"Failed to parse model ID {self._model_str}, "
                f"proceeding without slot reservation: {e}"
            )
            return True  # Proceed without reservation

        self._reserved = gateway_tracker.try_reserve_slot(
            gateway_id=self._gateway_id,
            endpoint_category=self._endpoint_category,
            compute_type=self._compute_type,
            request_id=self._request_id,
            routing_key=self._routing_key,
            max_concurrent_requests=max_concurrent_requests,
        )

        if self._reserved:
            logger.debug(
                f"Slot reserved: {self._endpoint_category}/{self._compute_type} "
                f"on {self._gateway_id}"
            )

        return self._reserved

    def release(self) -> None:
        """Release reserved slot. Idempotent."""
        if not self._reserved or not self._gateway_id:
            return

        from src.core.gateway_tracker import gateway_tracker

        gateway_tracker.complete_request(self._gateway_id, self._request_id)
        self._reserved = False
        logger.debug(
            f"🔓 Edge slot RELEASED: gw={self._gateway_id}, "
            f"model={self._model_str}, req={self._request_id[:8]}"
        )
