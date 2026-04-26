"""
Federation model load orchestrator.

Handles explicit model loading on Remote Stargates before forwarding requests.

INVARIANT: ∀ federated_request ⟹ model_loaded_on_remote ∨ error_raised
INVARIANT: ∀ model_id parameter: isinstance(model_id, ModelId)
INVARIANT: ∀ (gateway_id, routing_key): |in_flight_loads| ≤ 1 (single-flight)
INVARIANT: ∀ loading_request: routing_key tracked for eviction protection
"""

from __future__ import annotations

import asyncio
import time
import uuid
from contextlib import suppress
from typing import TYPE_CHECKING, Any

import httpx
from fastapi import HTTPException
from universal_logging import get_logger
from universal_protocol import ErrorCode, error_envelope, get_http_status

from .config import DEFAULT_ORCHESTRATION_CONFIG, OrchestrationConfig

if TYPE_CHECKING:
    from model_id import ModelId

    from ...common.types import FederatedGateway
    from ..routing.orchestrator import MasterRequestTracker
    from .manager.federated_gateway_manager import FederatedGatewayManager
    from .metrics import OrchestrationMetrics
    from .routing.forward import FederatedRequestForwarder

logger = get_logger(__name__)


async def _emit_federation_load_debug(
    step: str,
    gateway_id: str,
    model_id: str,
    request_id: str,
    **extra: Any,
) -> None:
    return None


def _format_elapsed_ms(elapsed_ms: float) -> str:
    """Render elapsed time for user-facing load errors."""
    if elapsed_ms >= 1000:
        return f"{elapsed_ms / 1000:.1f}s"
    return f"{elapsed_ms:.0f}ms"


def _load_error(
    code: str,
    message: str,
    model_id: ModelId,
    gateway_id: str,
    *,
    retryable: bool = True,
    elapsed_ms: int | None = None,
    timeout_budget_s: float | int | None = None,
) -> HTTPException:
    """Build structured HTTPException for load failures.

    Uses error_envelope so the capacity retry loop in chat.py
    recognises these as retryable (or not) via _is_capacity_error().
    """
    data: dict[str, Any] = {"model_id": str(model_id), "gateway_id": gateway_id}
    if elapsed_ms is not None:
        data["elapsed_ms"] = elapsed_ms
    if timeout_budget_s is not None:
        data["timeout_budget_s"] = timeout_budget_s

    return HTTPException(
        status_code=get_http_status(code),
        detail=error_envelope(
            code=code,
            message=message,
            source="master",
            retryable=retryable,
            data=data,
        ),
    )


class FederatedLoadOrchestrator:
    """
    Orchestrates model loading on Remote Stargates.

    CRITICAL INVARIANTS:
    - Single-flight: Only one load request per (remote, model) at a time
    - HTTP response is AUTHORITATIVE: Load completion = HTTP 2xx
    - Retry policy: 5xx errors retried, 4xx errors fail immediately
    - Telemetry is HINT only: May skip load if fresh, must handle failure
    - CancelledError MUST propagate (never swallowed)

    FORWARDER CONTRACT:
    - `forward_model_load_request()` returns structured dict, not raises
    - Dict contains: status ("ok" | "failed"), status_code, message
    - Orchestrator decides retry based on dict contents
    - ModelId serialization happens IN THE FORWARDER

    RETRY OWNERSHIP:
    - Gateways do NOT retry; all retry logic is orchestrator-driven
    - This ensures single source of truth for retry decisions
    """

    def __init__(
        self,
        forwarder: FederatedRequestForwarder,
        config: OrchestrationConfig | None = None,
        gateway_manager: FederatedGatewayManager | None = None,
        metrics: OrchestrationMetrics | None = None,
        event_bus: Any | None = None,
        request_tracker: MasterRequestTracker | None = None,
    ):
        """
        Initialize orchestrator.

        Args:
            forwarder: FederatedRequestForwarder for authenticated calls
            config: OrchestrationConfig (uses defaults if None)
            gateway_manager: Optional for telemetry queries
            metrics: Optional for observability
            event_bus: Optional event bus for load events
            request_tracker: MasterRequestTracker for routing_key eviction protection

        Raises:
            ValueError: If forwarder is None
        """
        if forwarder is None:
            raise ValueError("FederatedLoadOrchestrator requires forwarder")

        self._forwarder = forwarder
        self._config = config or DEFAULT_ORCHESTRATION_CONFIG
        self._gateway_manager = gateway_manager
        self._metrics = metrics
        self._event_bus = event_bus
        self._request_tracker = request_tracker

        # Single-flight mechanism
        self._pending_loads: dict[tuple[str, str], asyncio.Future[bool]] = {}

    def _track_routing_key(
        self, gateway_id: str, request_id: str, routing_key: str
    ) -> None:
        """
        Track routing_key during model loading for eviction protection.

        Called BEFORE initiating load to ensure eviction planner sees this
        model as "in-use" even before generation starts.
        """
        if self._request_tracker is None:
            logger.debug(
                "No request_tracker - routing_key eviction protection disabled"
            )
            return

        self._request_tracker.track_routing_key(gateway_id, request_id, routing_key)
        logger.debug(
            f"🔒 Loading request tracked: req={request_id[:8]}, "
            f"routing_key={routing_key}, gateway={gateway_id}"
        )

    def _release_routing_key(self, request_id: str) -> None:
        """
        Release routing_key tracking on load failure/cancellation.

        Called in error paths to prevent stale eviction protection.
        Idempotent - safe to call if never tracked or already released.
        """
        if self._request_tracker is None:
            return

        released = self._request_tracker.release_routing_key(request_id)
        if released:
            logger.debug(f"🔓 Loading request released: req={request_id[:8]}")

    @property
    def forwarder(self) -> FederatedRequestForwarder:
        """Expose forwarder for router-only eviction."""
        return self._forwarder

    @property
    def metrics(self) -> OrchestrationMetrics | None:
        """Get metrics instance (for endpoint wiring)."""
        return self._metrics

    @property
    def config(self) -> OrchestrationConfig:
        """Get orchestration config."""
        return self._config

    async def _should_skip_load_per_telemetry(
        self,
        gateway: FederatedGateway,
        model_id: ModelId,
    ) -> bool:
        """
        Check if load can be skipped based on telemetry HINT.

        INVARIANT: Caller MUST handle failure if telemetry is wrong.

        Returns:
            True if telemetry hints model is loaded and fresh
        """
        if self._gateway_manager is None:
            return False

        if not self._gateway_manager.is_telemetry_fresh(
            gateway.gateway_id,
            self._config.telemetry_staleness_threshold,
        ):
            logger.debug(
                f"📊 Telemetry stale for {gateway.gateway_id}, forcing explicit load"
            )
            if self._metrics:
                self._metrics.record_stale_telemetry()
            return False

        if self._gateway_manager.is_model_believed_loaded(gateway.gateway_id, model_id):
            logger.info(f"📊 Telemetry hints {model_id} loaded on {gateway.gateway_id}")
            return True

        return False

    async def ensure_model_loaded_on_remote(
        self,
        gateway: FederatedGateway,
        model_id: ModelId,
        *,
        sticky: bool = True,
        request_id: str | None = None,
    ) -> bool:
        """
        Ensure model is loaded on Remote gateway before forwarding requests.

        SINGLE-FLIGHT: Only one load request per (gateway, model) at a time.
        Concurrent callers await the same future, and completed futures remain
        cached briefly to absorb bursty follow-up callers.

        EVICTION PROTECTION: Tracks routing_key for ALL callers (primary and
        coalesced) to prevent eviction during loading + pre-generation phases.
        Released on load failure; persists until generation completes on success.

        Flow:
        1. Track routing_key for eviction protection (BEFORE any await)
        2. Check telemetry hint (skip load if model believed loaded and fresh)
        3. Atomic check-and-insert: either find existing future or
           create+register new one
        4. If existing future found: await it (coalesced request)
        5. If we created the future: call Remote's
           /api/v1/federation/models/load endpoint
        6. HTTP 2xx response is authoritative (no telemetry event waiting)

        Telemetry-gated load optimization (Phase 4):
        - Checks telemetry before issuing load command
        - Skips load if model believed loaded and telemetry fresh
        - Falls back to explicit load if telemetry stale or unavailable

        Args:
            gateway: FederatedGateway to load model on
            model_id: Model to load (ModelId object, NOT string)
            sticky: Whether to use sticky routing
            request_id: Optional correlation ID from original request (for tracing)

        Returns:
            True if model loaded successfully

        Raises:
            HTTPException: If load fails or times out. ALWAYS explicit to client.
        """
        # Cloud gateways (backend_type == "cloud_api") need no remote load:
        # models are always available via the cloud proxy.
        if gateway.is_cloud:
            logger.debug(
                f"☁️ Skipping load for cloud gateway {gateway.gateway_id}: "
                f"{model_id} is always available via cloud proxy"
            )
            return True

        routing_key = model_id.routing_key
        # Tuple key: collision-proof (string keys fail if gateway_id or
        # routing_key contains separator)
        load_key = (gateway.gateway_id, routing_key)

        # Generate request_id if not provided
        if request_id is None:
            request_id = str(uuid.uuid4())

        # EVICTION PROTECTION: Track routing_key BEFORE any await
        # This ensures eviction planner sees this model as "in-use" during:
        # - Model loading phase
        # - Token counting phase (after load, before generation)
        # Released on load failure; persists until generation completes on success.
        # INV: routing_key ∈ get_routing_keys_in_use_globally() until generation done
        self._track_routing_key(gateway.gateway_id, request_id, routing_key)
        load_succeeded = False

        try:
            await _emit_federation_load_debug(
                "ensure_enter",
                gateway.gateway_id,
                str(model_id),
                request_id,
                sticky=sticky,
                remote_id=gateway.remote_stargate_id,
            )
            # PHASE 4: Check telemetry hint BEFORE acquiring lock
            #
            # NOTE:
            # Telemetry is still a HINT, not an authority. A TOCTOU window remains
            # where a model may be unloading on the Remote/Gateway while the Master
            # still believes it is loaded (unload event not yet applied). We accept
            # that bounded risk here to suppress redundant load storms when fresh
            # telemetry already confirms the model is resident.
            if await self._should_skip_load_per_telemetry(gateway, model_id):
                await _emit_federation_load_debug(
                    "telemetry_skip",
                    gateway.gateway_id,
                    str(model_id),
                    request_id,
                )
                logger.info(
                    f"📊 Telemetry confirms {model_id} loaded on "
                    f"{gateway.gateway_id} - skipping"
                )
                load_succeeded = True
                return True

            # Single-flight: check-and-insert is atomic (no await between them).
            if load_key in self._pending_loads:
                existing_future = self._pending_loads[load_key]
                await _emit_federation_load_debug(
                    "coalesced_wait",
                    gateway.gateway_id,
                    str(model_id),
                    request_id,
                )
                if self._metrics:
                    self._metrics.record_coalesced_caller()
                logger.debug(f"⏳ Coalescing with existing load: {load_key}")
            else:
                future: asyncio.Future[bool] = (
                    asyncio.get_running_loop().create_future()
                )
                self._pending_loads[load_key] = future
                existing_future = None
                await _emit_federation_load_debug(
                    "primary_begin",
                    gateway.gateway_id,
                    str(model_id),
                    request_id,
                )
                if self._metrics:
                    self._metrics.record_primary_caller()
                logger.debug(f"🔄 Primary loader for: {load_key}")

            if existing_future is not None:
                # Await the existing load operation's result
                # Use shield() to prevent cancellation of the original task
                # Note: Our tracking persists - released when our generation completes
                result = await self._await_coalesced_load(
                    existing_future, model_id, load_key
                )
                load_succeeded = result
                return result

            # We're the primary caller - perform load
            start_time = time.monotonic()
            try:
                # NOTE: Loading state is marked in gateway_selection.py immediately
                # after routing decision, before this call. This ensures subsequent
                # routing decisions see the updated state.

                # Emit load requested event
                if self._event_bus:
                    from src.scheduling.events import FederationLoadRequested

                    asyncio.create_task(
                        self._event_bus.publish_nowait(
                            FederationLoadRequested(
                                request_id=request_id,
                                target_remote=gateway.remote_stargate_id,
                                model_id=str(model_id),
                            )
                        )
                    )

                await _emit_federation_load_debug(
                    "remote_call_start",
                    gateway.gateway_id,
                    str(model_id),
                    request_id,
                )
                await self._call_remote_load(gateway, model_id, sticky, request_id)
                duration = time.monotonic() - start_time
                duration_ms = int(duration * 1000)
                await _emit_federation_load_debug(
                    "remote_call_done",
                    gateway.gateway_id,
                    str(model_id),
                    request_id,
                    duration_ms=duration_ms,
                )
                if self._metrics:
                    self._metrics.record_load_operation_success(duration)
                logger.info(f"✅ Model {model_id} loaded on {gateway.gateway_id}")

                if self._gateway_manager:
                    self._gateway_manager.restore_model_capacity(gateway, model_id)

                # Emit load confirmed event
                if self._event_bus:
                    from src.scheduling.events import FederationLoadConfirmed

                    asyncio.create_task(
                        self._event_bus.publish_nowait(
                            FederationLoadConfirmed(
                                request_id=request_id,
                                remote_id=gateway.remote_stargate_id,
                                model_id=str(model_id),
                                duration_ms=duration_ms,
                            )
                        )
                    )

                future.set_result(True)
                load_succeeded = True
                return True

            except Exception as e:
                # NOTE: CancelledError is BaseException, NOT Exception -
                # intentionally not caught here.
                # If primary is cancelled, CancelledError propagates; finally
                # block handles followers.
                duration = time.monotonic() - start_time
                duration_ms = int(duration * 1000)
                if self._metrics:
                    self._metrics.record_load_operation_failure(duration)
                logger.error(
                    f"❌ Failed to load {model_id} on {gateway.gateway_id}: {e}"
                )

                await _emit_federation_load_debug(
                    "remote_call_failed",
                    gateway.gateway_id,
                    str(model_id),
                    request_id,
                    duration_ms=duration_ms,
                    error_type=type(e).__name__,
                    error=str(e),
                )
                # Emit load failed event
                if self._event_bus:
                    from src.scheduling.events import FederationLoadFailed

                    asyncio.create_task(
                        self._event_bus.publish_nowait(
                            FederationLoadFailed(
                                request_id=request_id,
                                remote_id=gateway.remote_stargate_id,
                                model_id=str(model_id),
                                error=str(e),
                            )
                        )
                    )

                # Ensure future is resolved for coalesced callers
                if not future.done():
                    future.set_exception(e)
                raise

            finally:
                # CRITICAL: Clear loading state on any exit path (success handled
                # by telemetry, failure/cancellation handled here)
                # Success case: telemetry MODEL_LOADED will reconcile
                # Failure/Cancellation case: no telemetry arrives, so we must clear
                # immediately.
                if not future.done() or (
                    future.done() and future.exception() is not None
                ):
                    # Task failed or was cancelled - clear loading state
                    if self._gateway_manager:
                        await _emit_federation_load_debug(
                            "clear_loading_start",
                            gateway.gateway_id,
                            str(model_id),
                            request_id,
                        )
                        await self._gateway_manager.clear_model_loading(
                            gateway.gateway_id, model_id
                        )
                        await _emit_federation_load_debug(
                            "clear_loading_done",
                            gateway.gateway_id,
                            str(model_id),
                            request_id,
                        )

                # TTL eviction: keep completed future for stampede protection
                if self._pending_loads.get(load_key) is future:
                    loop = asyncio.get_running_loop()
                    _f = future
                    loop.call_later(
                        10.0,
                        lambda k=load_key, f=_f: (
                            self._pending_loads.pop(k, None)
                            if self._pending_loads.get(k) is f
                            else None
                        ),
                    )

                # If future wasn't resolved (e.g., task cancelled via
                # CancelledError), resolve it now
                # This ensures followers NEVER hang: they always get a deterministic
                # result.
                if not future.done():
                    future.set_exception(
                        _load_error(
                            ErrorCode.RESOURCE_UNAVAILABLE,
                            f"Load operation cancelled for {model_id}",
                            model_id,
                            gateway.gateway_id,
                        )
                    )

        except BaseException:
            # CRITICAL: Use BaseException to catch CancelledError (not Exception!)
            # CancelledError is a BaseException in Python 3.8+
            # Load failed or cancelled - release eviction protection tracking
            # (model won't be used, no point protecting from eviction)
            if not load_succeeded:
                self._release_routing_key(request_id)
            raise

    async def _await_coalesced_load(
        self,
        future: asyncio.Future[bool],
        model_id: ModelId,
        load_key: tuple[str, str],
    ) -> bool:
        """
        Await an existing load operation (coalesced request).

        Uses asyncio.shield() to prevent follower cancellation from affecting primary.

        CANCELLATION SEMANTICS:
        - asyncio.shield() protects the INNER future (primary's HTTP call)
        - If follower is cancelled, CancelledError is raised HERE, not in primary
        - Primary continues unaffected; follower's cancel just exits this wait
        - This aligns with CancelledError propagation: follower gets cancelled,
          primary completes normally, single-flight cleanup happens in finally block

        NOTE: shield() does NOT prevent the outer wait_for timeout from firing.
        """
        try:
            await _emit_federation_load_debug(
                "coalesced_wait_start",
                load_key[0],
                str(model_id),
                "coalesced",
            )
            result = await asyncio.wait_for(
                asyncio.shield(future),
                timeout=self._config.coalesce_wait_timeout,  # Use config
            )
            await _emit_federation_load_debug(
                "coalesced_wait_done",
                load_key[0],
                str(model_id),
                "coalesced",
                result=result,
            )
            logger.debug(f"✅ Coalesced load completed: {load_key}")
            return result
        except TimeoutError:
            await _emit_federation_load_debug(
                "coalesced_wait_timeout",
                load_key[0],
                str(model_id),
                "coalesced",
                timeout_s=self._config.coalesce_wait_timeout,
            )
            logger.warning(
                f"⏱️ Follower timed out waiting for {load_key}, primary continues"
            )
            raise _load_error(
                ErrorCode.LOAD_TIMEOUT,
                f"Timeout waiting for existing load of {model_id}",
                model_id,
                load_key[0],  # gateway_id
            )
        except HTTPException:
            raise
        except Exception as e:
            await _emit_federation_load_debug(
                "coalesced_wait_failed",
                load_key[0],
                str(model_id),
                "coalesced",
                error_type=type(e).__name__,
                error=str(e),
            )
            logger.error(f"❌ Coalesced load failed for {model_id}: {e}")
            raise _load_error(
                ErrorCode.RESOURCE_UNAVAILABLE,
                f"Remote load failed for {model_id}: {e}",
                model_id,
                load_key[0],  # gateway_id
            )

    async def _call_remote_load(
        self,
        gateway: FederatedGateway,
        model_id: ModelId,
        sticky: bool,
        request_id: str,
    ) -> dict:
        """
        Call Remote's model load endpoint with retry logic.

        FORWARDER CONTRACT:
        - `forward_model_load_request()` returns a **structured dictionary**
        - Dictionary contains: status, message, and exception details if any
        - Forwarder does NOT raise on HTTP errors; orchestrator decides retry
          based on dict
        - ModelId serialization happens IN THE FORWARDER, not here

        INVARIANTS:
        - Only 5xx errors and transient failures trigger retry
        - 4xx errors fail immediately (client error, won't succeed on retry)
        - Total attempts = 1 (initial) + load_retry_count (retries)
        - CancelledError MUST re-raise immediately (never swallowed)
        - Programming errors (TypeError, AttributeError) MUST re-raise (fail-fast)

        TIMEOUT POLICY:
        - Wall-clock timeout (asyncio.TimeoutError): NO retry - returns 504 immediately
        - HTTP phase timeout (httpx.TimeoutException): YES retry - transient network

        NAMING CONVENTION:
        - attempt_index: 1-indexed attempt number (1 = first attempt, 2+ = retries)
        - retry_index: Same as attempt_index (both 1-indexed; simplified semantics)
        - Delays use retry_index = attempt_index; metrics use attempt_index

        Args:
            gateway: Remote gateway
            model_id: Model to load (ModelId object; serialized by forwarder)
            sticky: Sticky routing flag
            request_id: Correlation ID for tracing

        Returns:
            Response dict from Remote

        Raises:
            HTTPException: On failure (4xx, 5xx after retries, timeout)
            CancelledError: If task cancelled (always re-raised)
        """
        max_attempts = 1 + self._config.load_retry_count  # initial + retries

        # Defensive: initialize with fallback error (should always be overwritten)
        last_error: HTTPException = _load_error(
            ErrorCode.RESOURCE_UNAVAILABLE,
            f"Failed to load {model_id} after {max_attempts} attempts (unknown error)",
            model_id,
            gateway.gateway_id,
        )

        # Track if we've already recorded exhaustion (prevent double-counting)
        exhaustion_recorded = False

        for attempt_index in range(1, max_attempts + 1):
            attempt_start = time.monotonic()
            try:
                await _emit_federation_load_debug(
                    "attempt_start",
                    gateway.gateway_id,
                    str(model_id),
                    request_id,
                    attempt=attempt_index,
                    max_attempts=max_attempts,
                )
                logger.info(
                    f"🔄 Load attempt {attempt_index}/{max_attempts}: "
                    f"{gateway.remote_stargate_url} for {model_id}",
                    extra={"request_id": request_id},
                )

                # Wall-clock timeout authority (180s default)
                # NOTE: model_id is ModelId object; forwarder handles serialization
                remote_call = asyncio.create_task(
                    self._forwarder.forward_model_load_request(
                        gateway=gateway,
                        model_id=model_id,  # ModelId object, NOT str
                        sticky=sticky,
                        request_id=request_id,
                    )
                )
                result = await asyncio.wait_for(
                    remote_call,
                    timeout=self._config.load_timeout,
                )

                elapsed_ms = (time.monotonic() - attempt_start) * 1000
                await _emit_federation_load_debug(
                    "attempt_response",
                    gateway.gateway_id,
                    str(model_id),
                    request_id,
                    attempt=attempt_index,
                    elapsed_ms=int(elapsed_ms),
                    status=result.get("status"),
                    status_code=result.get("status_code"),
                )
                logger.debug(f"📥 Remote load response: {result}")

                # Forwarder returns structured dict; check for failure status
                if result.get("status") == "failed":
                    status_code = result.get("status_code", 503)

                    # 4xx = client error, don't retry
                    if status_code < 500:
                        logger.error(
                            f"❌ Client error loading {model_id}: {status_code} "
                            f"(not retrying 4xx)"
                        )
                        if self._metrics:
                            self._metrics.record_load_operation_failure(elapsed_ms)
                        await _emit_federation_load_debug(
                            "attempt_client_error",
                            gateway.gateway_id,
                            str(model_id),
                            request_id,
                            attempt=attempt_index,
                            status_code=status_code,
                            message=result.get("message", "unknown"),
                        )
                        raise _load_error(
                            ErrorCode.INVALID_REQUEST,
                            f"Remote load failed: {result.get('message', 'unknown')}",
                            model_id,
                            gateway.gateway_id,
                            retryable=False,
                        )

                    # 5xx = server error, may retry
                    last_error = _load_error(
                        ErrorCode.RESOURCE_UNAVAILABLE,
                        f"Remote load failed: {result.get('message', 'unknown')}",
                        model_id,
                        gateway.gateway_id,
                    )

                    if attempt_index < max_attempts:
                        # retry_index: attempt 1 → 1, attempt 2 → 2, etc.
                        retry_index = attempt_index
                        delay = self._config.calculate_retry_delay(retry_index)
                        logger.warning(
                            f"⚠️ Load attempt {attempt_index} failed ({status_code}), "
                            f"retrying in {delay:.1f}s..."
                        )
                        if self._metrics:
                            self._metrics.record_retry()
                        await _emit_federation_load_debug(
                            "attempt_retry",
                            gateway.gateway_id,
                            str(model_id),
                            request_id,
                            attempt=attempt_index,
                            status_code=status_code,
                            delay_s=delay,
                        )
                        await asyncio.sleep(delay)
                        continue
                    else:
                        logger.error(
                            f"❌ All {max_attempts} attempts exhausted for {model_id}"
                        )
                        if self._metrics:
                            self._metrics.record_load_operation_failure(elapsed_ms)
                            if not exhaustion_recorded:
                                self._metrics.record_retries_exhausted()
                                exhaustion_recorded = True
                        raise last_error

                # Success
                logger.info(
                    f"✅ Model {model_id} loaded on {gateway.gateway_id} "
                    f"(attempt {attempt_index}/{max_attempts}, {elapsed_ms:.0f}ms)"
                )
                if self._metrics:
                    self._metrics.record_load_operation_success(elapsed_ms)
                await _emit_federation_load_debug(
                    "attempt_success",
                    gateway.gateway_id,
                    str(model_id),
                    request_id,
                    attempt=attempt_index,
                    elapsed_ms=int(elapsed_ms),
                )
                return result

            except asyncio.CancelledError:
                # CRITICAL: Never swallow cancellation (BaseException)
                # Let it propagate; single-flight cleanup happens in the caller's
                # finally block.
                await _emit_federation_load_debug(
                    "attempt_cancelled",
                    gateway.gateway_id,
                    str(model_id),
                    request_id,
                    attempt=attempt_index,
                )
                logger.warning(
                    f"🚫 Load cancelled for {model_id} (attempt {attempt_index})"
                )
                raise

            except TimeoutError:
                elapsed_ms = (time.monotonic() - attempt_start) * 1000
                if (
                    "remote_call" in locals()
                    and remote_call.done()
                    and not remote_call.cancelled()
                ):
                    inner_exc = remote_call.exception()
                    inner_summary = (
                        f"{type(inner_exc).__name__}: {inner_exc}"
                        if inner_exc is not None
                        else "inner timeout with no exception details"
                    )
                    last_error = _load_error(
                        ErrorCode.LOAD_TIMEOUT,
                        f"Remote load for {model_id} on {gateway.gateway_id} failed "
                        f"after {_format_elapsed_ms(elapsed_ms)} before the "
                        f"{self._config.load_timeout}s master timeout budget expired: "
                        f"{inner_summary}",
                        model_id,
                        gateway.gateway_id,
                        elapsed_ms=int(elapsed_ms),
                        timeout_budget_s=self._config.load_timeout,
                    )
                    logger.error(
                        f"⏱️ Upstream timeout surfaced for {model_id} after "
                        f"{_format_elapsed_ms(elapsed_ms)} "
                        f"(master budget {self._config.load_timeout}s): {inner_summary}"
                    )
                    if self._metrics:
                        self._metrics.record_load_operation_failure(elapsed_ms)
                        if not exhaustion_recorded:
                            self._metrics.record_retries_exhausted()
                            exhaustion_recorded = True
                    await _emit_federation_load_debug(
                        "attempt_inner_timeout",
                        gateway.gateway_id,
                        str(model_id),
                        request_id,
                        attempt=attempt_index,
                        elapsed_ms=int(elapsed_ms),
                        inner_error=inner_summary,
                    )
                    break

                if "remote_call" in locals():
                    remote_call.cancel()
                    with suppress(asyncio.CancelledError):
                        await remote_call

                # Wall-clock timeout = operation too slow/hung
                # DON'T retry: Remote may still be loading, but waiting longer is
                # unreasonable.
                # Next request will re-issue ensure_model_loaded() which is idempotent
                last_error = _load_error(
                    ErrorCode.LOAD_TIMEOUT,
                    f"Timeout loading {model_id} on {gateway.gateway_id} after "
                    f"{_format_elapsed_ms(elapsed_ms)} "
                    f"(budget {self._config.load_timeout}s)",
                    model_id,
                    gateway.gateway_id,
                    elapsed_ms=int(elapsed_ms),
                    timeout_budget_s=self._config.load_timeout,
                )
                logger.error(
                    f"⏱️ Load timeout for {model_id} after "
                    f"{_format_elapsed_ms(elapsed_ms)} - "
                    "not retrying (Remote may still be loading; "
                    "telemetry will update state)"
                )
                if self._metrics:
                    self._metrics.record_load_operation_failure(elapsed_ms)
                    if not exhaustion_recorded:
                        self._metrics.record_retries_exhausted()
                        exhaustion_recorded = True
                await _emit_federation_load_debug(
                    "attempt_wall_timeout",
                    gateway.gateway_id,
                    str(model_id),
                    request_id,
                    attempt=attempt_index,
                    elapsed_ms=int(elapsed_ms),
                    timeout_budget_s=self._config.load_timeout,
                )
                break  # No retry for wall-clock timeout

            except httpx.TimeoutException as e:
                # HTTP phase timeout (connect/read) = transient network issue
                # YES retry: This is likely recoverable
                elapsed_ms = (time.monotonic() - attempt_start) * 1000
                last_error = _load_error(
                    ErrorCode.LOAD_TIMEOUT,
                    f"HTTP timeout loading {model_id} on {gateway.gateway_id}",
                    model_id,
                    gateway.gateway_id,
                )
                logger.warning(f"⏱️ HTTP phase timeout loading {model_id}: {e}")
                await _emit_federation_load_debug(
                    "attempt_http_timeout",
                    gateway.gateway_id,
                    str(model_id),
                    request_id,
                    attempt=attempt_index,
                    elapsed_ms=int(elapsed_ms),
                    error=str(e),
                )

                if attempt_index < max_attempts:
                    retry_index = attempt_index
                    delay = self._config.calculate_retry_delay(retry_index)
                    logger.warning(f"⚠️ Retrying in {delay:.1f}s...")
                    if self._metrics:
                        self._metrics.record_retry()
                    await asyncio.sleep(delay)
                else:
                    logger.error(
                        f"❌ All {max_attempts} attempts exhausted for {model_id}"
                    )
                    if self._metrics:
                        self._metrics.record_load_operation_failure(elapsed_ms)
                        if not exhaustion_recorded:
                            self._metrics.record_retries_exhausted()
                            exhaustion_recorded = True

            except httpx.RequestError as e:
                # Connection errors = transient, retry
                elapsed_ms = (time.monotonic() - attempt_start) * 1000
                last_error = _load_error(
                    ErrorCode.GATEWAY_DISCONNECTED,
                    f"Connection error to {gateway.gateway_id}: {e}",
                    model_id,
                    gateway.gateway_id,
                )
                logger.error(f"🔌 Connection error loading {model_id}: {e}")
                await _emit_federation_load_debug(
                    "attempt_request_error",
                    gateway.gateway_id,
                    str(model_id),
                    request_id,
                    attempt=attempt_index,
                    elapsed_ms=int(elapsed_ms),
                    error=str(e),
                )

                if attempt_index < max_attempts:
                    retry_index = attempt_index
                    delay = self._config.calculate_retry_delay(retry_index)
                    logger.warning(f"⚠️ Retrying in {delay:.1f}s...")
                    if self._metrics:
                        self._metrics.record_retry()
                    await asyncio.sleep(delay)
                else:
                    if self._metrics:
                        self._metrics.record_load_operation_failure(elapsed_ms)
                        if not exhaustion_recorded:
                            self._metrics.record_retries_exhausted()
                            exhaustion_recorded = True

            except (TypeError, AttributeError, KeyError) as e:
                # Programming errors = fail-fast, never retry
                # These indicate bugs, not operational issues
                logger.error(
                    f"🐛 Programming error loading {model_id}: {type(e).__name__}: {e}"
                )
                await _emit_federation_load_debug(
                    "attempt_programming_error",
                    gateway.gateway_id,
                    str(model_id),
                    request_id,
                    attempt=attempt_index,
                    error_type=type(e).__name__,
                    error=str(e),
                )
                raise  # Re-raise to crash loudly

            except HTTPException:
                # HTTPException from somewhere else (not our structured response)
                # Already handled above via result dict; this shouldn't happen
                raise

            except Exception as e:
                # Unexpected operational error = unknown state, don't retry
                elapsed_ms = (time.monotonic() - attempt_start) * 1000
                last_error = _load_error(
                    ErrorCode.UNEXPECTED_ERROR,
                    f"Unexpected error loading {model_id}: {e}",
                    model_id,
                    gateway.gateway_id,
                    retryable=False,
                )
                logger.error(
                    f"❌ Unexpected error loading {model_id}: {type(e).__name__}: {e}"
                )
                await _emit_federation_load_debug(
                    "attempt_unexpected_error",
                    gateway.gateway_id,
                    str(model_id),
                    request_id,
                    attempt=attempt_index,
                    elapsed_ms=int(elapsed_ms),
                    error_type=type(e).__name__,
                    error=str(e),
                )
                if self._metrics:
                    self._metrics.record_load_operation_failure(elapsed_ms)
                    if not exhaustion_recorded:
                        self._metrics.record_retries_exhausted()
                        exhaustion_recorded = True
                break  # Don't retry unexpected errors

        raise last_error
