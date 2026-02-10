"""
Routing reservation methods for GlobalModelLoadCoordinator.

Provides reservation methods to prevent parallel pipeline steps
from racing to select the same gateway.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

from universal_logging import get_logger

if TYPE_CHECKING:
    from .global_coordinator import GlobalModelLoadCoordinator

logger = get_logger(__name__)


async def reserve_for_routing(
    coordinator: GlobalModelLoadCoordinator,
    model: str,
    requester_id: str,
    ttl_seconds: float = 5.0,
) -> tuple[bool, str | None, asyncio.Event | None]:
    """
    Reserve a model for routing decision (short-lived, auto-expires).

    Prevents parallel pipeline steps from racing to select the same gateway.
    Called BEFORE gateway selection, not after.

    Invariant: ∀ routing_key, ∃! reservation holder (or none)

    Args:
        coordinator: GlobalModelLoadCoordinator instance
        model: Model ID to reserve
        requester_id: Unique ID for this routing request (e.g., "pipeline-abc123")
        ttl_seconds: Reservation TTL (auto-expires to prevent deadlocks)

    Returns:
        (can_reserve, redirect_gateway, wait_event)
        - (True, None, None): Reservation granted, proceed with routing
        - (False, gateway_name, event): Model loading on gateway, wait for event
        - (False, None, event): Another reservation active, wait for event
    """
    routing_key = coordinator._get_routing_key(model)

    # Priority 0: Already loaded? Route there immediately
    if routing_key in coordinator._models_loaded:
        gateway = coordinator._models_loaded[routing_key]
        logger.debug(f"📍 {model} already loaded on {gateway} (rkey={routing_key})")
        return False, gateway, None

    # Priority 1: Currently loading? Wait for it
    if routing_key in coordinator._models_loading:
        gateway, completion_event = coordinator._models_loading[routing_key]
        logger.debug(
            f"⏳ {model} loading on {gateway}, returning wait event "
            f"(rkey={routing_key})"
        )
        return False, gateway, completion_event

    # Priority 2: Another routing reservation active?
    if routing_key in coordinator._routing_reservations:
        existing_requester, expiry_time = coordinator._routing_reservations[routing_key]
        if time.time() < expiry_time:
            # Create event for this waiter (event-driven, not polling)
            wait_event = asyncio.Event()
            if routing_key not in coordinator._routing_reservation_waiters:
                coordinator._routing_reservation_waiters[routing_key] = []
            coordinator._routing_reservation_waiters[routing_key].append(wait_event)
            waiter_count = len(coordinator._routing_reservation_waiters[routing_key])
            logger.debug(
                f"🔒 {model} routing reserved by {existing_requester}, "
                f"requester {requester_id} will wait ({waiter_count} waiters)"
            )
            return False, None, wait_event
        else:
            # Expired - clean up and continue
            logger.debug(
                f"⏰ {model} routing reservation expired, cleaning up "
                f"(rkey={routing_key})"
            )
            del coordinator._routing_reservations[routing_key]

    # Grant reservation
    expiry = time.time() + ttl_seconds
    coordinator._routing_reservations[routing_key] = (requester_id, expiry)
    logger.info(
        f"✅ {model} routing reserved by {requester_id} "
        f"(TTL={ttl_seconds}s, rkey={routing_key})"
    )
    return True, None, None


async def release_routing_reservation(
    coordinator: GlobalModelLoadCoordinator,
    model: str,
    requester_id: str,
) -> None:
    """
    Release a routing reservation.

    Called after gateway selection completes (success or failure).
    Safe to call even if reservation expired or was never held.
    Wakes up any waiters (event-driven).
    """
    routing_key = coordinator._get_routing_key(model)

    if routing_key in coordinator._routing_reservations:
        owner, _ = coordinator._routing_reservations[routing_key]
        if owner == requester_id:
            del coordinator._routing_reservations[routing_key]

            # Wake up all waiters (event-driven, not polling)
            if routing_key in coordinator._routing_reservation_waiters:
                waiters = coordinator._routing_reservation_waiters[routing_key]
                logger.debug(
                    f"🔓 {model} routing reservation released by "
                    f"{requester_id}, waking {len(waiters)} waiter(s)"
                )
                for event in waiters:
                    event.set()
                del coordinator._routing_reservation_waiters[routing_key]
            else:
                logger.debug(
                    f"🔓 {model} routing reservation released by {requester_id}"
                )
        else:
            logger.debug(
                f"⚠️ {model} reservation release by {requester_id} "
                f"ignored - owned by {owner}"
            )


async def add_eviction_protection(
    coordinator: GlobalModelLoadCoordinator,
    model: str,
    requester_id: str,
    ttl_seconds: float = 300.0,
) -> None:
    """
    Add eviction protection for a model used by a pipeline step.

    Unlike reserve_for_routing(), this does NOT check if the model is
    already loaded. It unconditionally adds an entry to prevent eviction.

    Use case: Pipeline steps acquire models before executing. During
    execution, other steps may trigger eviction to load different models.
    This protection prevents evicting models that are actively in use.

    Args:
        coordinator: GlobalModelLoadCoordinator instance
        model: Model ID to protect
        requester_id: Unique ID for this protection (e.g., "pipeline-abc-step1")
        ttl_seconds: Protection TTL (auto-expires to prevent leaks)
    """
    routing_key = coordinator._get_routing_key(model)
    expiry = time.time() + ttl_seconds

    # Check if there's an existing reservation
    if routing_key in coordinator._routing_reservations:
        existing_owner, existing_expiry = coordinator._routing_reservations[routing_key]
        # If we already own it or it's expired, overwrite
        if existing_owner == requester_id or time.time() >= existing_expiry:
            coordinator._routing_reservations[routing_key] = (requester_id, expiry)
            logger.debug(
                f"🛡️ Eviction protection updated for {model} "
                f"(requester={requester_id}, ttl={ttl_seconds}s)"
            )
        else:
            # Another requester owns it - don't overwrite
            # This shouldn't happen in normal pipeline execution
            logger.debug(
                f"🛡️ Eviction protection for {model} already held by "
                f"{existing_owner}, not overwriting"
            )
    else:
        coordinator._routing_reservations[routing_key] = (requester_id, expiry)
        logger.debug(
            f"🛡️ Eviction protection created for {model} "
            f"(requester={requester_id}, ttl={ttl_seconds}s)"
        )


async def remove_eviction_protection(
    coordinator: GlobalModelLoadCoordinator,
    model: str,
    requester_id: str,
) -> None:
    """
    Remove eviction protection for a model.

    Called when a pipeline step completes. Safe to call if protection
    was never added or already removed.

    Args:
        coordinator: GlobalModelLoadCoordinator instance
        model: Model ID to unprotect
        requester_id: Requester that added the protection
    """
    routing_key = coordinator._get_routing_key(model)

    if routing_key in coordinator._routing_reservations:
        owner, _ = coordinator._routing_reservations[routing_key]
        if owner == requester_id:
            del coordinator._routing_reservations[routing_key]
            # Wake up any waiters (from reserve_for_routing)
            if routing_key in coordinator._routing_reservation_waiters:
                waiters = coordinator._routing_reservation_waiters[routing_key]
                for event in waiters:
                    event.set()
                del coordinator._routing_reservation_waiters[routing_key]
            logger.debug(
                f"🔓 Eviction protection removed for {model} (requester={requester_id})"
            )
