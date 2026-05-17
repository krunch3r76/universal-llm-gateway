"""
Remote mode model load API.

Receives load commands from Master and loads on local Gateway.

INVARIANT: ∀ operation: forwards_downstream(operation) (relay pattern)
INVARIANT: ¬∃ import from master/ (domain isolation)
"""

import asyncio

import httpx
from fastapi import APIRouter, HTTPException
from model_id import ModelId
from pydantic import BaseModel
from universal_logging import get_logger

logger = get_logger(__name__)


class ModelLoadRequest(BaseModel):
    """Request to load a model."""

    model_id: str
    sticky: bool = True


class ModelLoadResponse(BaseModel):
    """Response from model load request."""

    status: str  # "ok" | "failed"
    model_id: str
    message: str | None = None


class ModelUnloadRequest(BaseModel):
    """Request to unload a model."""

    model_id: str


class ModelUnloadResponse(BaseModel):
    """Response from model unload request."""

    status: str  # "ok" | "failed"
    model_id: str
    message: str | None = None


def create_model_router(
    gateway_manager, local_edge_client=None, relay_stargate_id: str | None = None
) -> APIRouter:
    """
    Create model load router for Remote mode.

    Remote only - no mode branching, no Master imports.

    Args:
        gateway_manager: Gateway manager for direct loading (Remote with Gateway)
        local_edge_client: Optional LocalEdgeClient for relay topology
        relay_stargate_id: Relay's stargate ID for federation auth headers

    Returns:
        APIRouter with /load endpoint
    """
    router = APIRouter(prefix="/api/v1/federation/models", tags=["federation-models"])

    def _edge_client_and_headers(timeout: float):
        """Build auth headers and an Edge UDS client."""
        headers = {"Content-Type": "application/json"}
        if relay_stargate_id and local_edge_client:
            headers["X-Federation-Source"] = relay_stargate_id
            headers["X-Federation-Key"] = local_edge_client._config.api_key
        client = httpx.AsyncClient(
            transport=httpx.AsyncHTTPTransport(
                uds=local_edge_client._config.socket_path
            ),
            timeout=timeout,
        )
        return client, headers

    @router.post("/load", response_model=ModelLoadResponse)
    async def load_model(body: ModelLoadRequest) -> ModelLoadResponse:
        """
        Load a model on local Gateway or forward to Edge.

        Called by Master before forwarding requests.
        Idempotent: returns success if model already loaded.

        CRITICAL: Waits for model to be fully loaded before returning success.
        Uses event-driven state from Gateway WebSocket (no polling).

        Relay Topology (Remote with local_edge):
            Remote → Edge (via Unix socket) → Gateway (inside Edge container)

        Direct Topology (Remote with Gateway):
            Remote → Gateway (via local gateway_manager)
        """
        model_id = ModelId.parse(body.model_id)

        logger.info(f"🔄 Received load command from Master: {model_id!r}")

        # Relay topology: Forward to Edge via Unix socket
        if local_edge_client:
            logger.info(f"📡 Forwarding load to Edge via Unix socket: {model_id!r}")
            try:
                # Slightly longer than Edge's own 180s outcome wait.
                client, headers = _edge_client_and_headers(185.0)
                async with client:
                    response = await client.post(
                        "http://edge/api/v1/federation/models/load",
                        json={
                            "model_id": model_id.synthetic_id,
                            "sticky": body.sticky,
                        },
                        headers=headers,
                    )
                    response.raise_for_status()
                    data = response.json()
                    logger.info(f"✅ Edge load response: {data}")
                    return ModelLoadResponse(**data)
            except httpx.HTTPStatusError as e:
                logger.error(f"❌ Edge load failed: HTTP {e.response.status_code}")
                raise HTTPException(
                    status_code=e.response.status_code,
                    detail=f"Edge load failed: {e.response.text}",
                )
            except Exception as e:
                logger.exception(f"❌ Failed to forward load to Edge: {e}")
                raise HTTPException(503, f"Edge connection failed: {e}")

        # Direct topology: Use local gateway_manager
        if not gateway_manager:
            logger.error("❌ Gateway manager not available and no Edge client")
            raise HTTPException(
                status_code=503,
                detail="Gateway manager unavailable - Remote not properly configured",
            )

        try:
            gateway = gateway_manager.require_gateway()

            # Diagnostic cache check only. All requests continue into
            # event-driven outcome tracking to avoid cache TOCTOU races.
            loaded_models = gateway.client.get_loaded_models()
            is_loaded = any(model_id == m for m in loaded_models)

            logger.info(
                f"🔍 [REMOTE] Load check: model={model_id!r}, "
                f"routing_key={model_id.routing_key}, "
                f"cache_loaded={is_loaded}, "
                f"loaded_models_count={len(loaded_models)}"
            )
            if not is_loaded and loaded_models:
                sample = sorted(loaded_models)[:5]
                logger.debug(f"🔍 [REMOTE] Loaded models: {sample}")

            # Setup outcome tracking BEFORE initiating load
            from .load_outcome import LoadFailedError, LoadOutcomeTracker

            ws_client = gateway.client.ws_client
            tracker = LoadOutcomeTracker(model_id)
            tracker.register(ws_client)

            try:
                # Initiate load (non-blocking, returns when request sent)
                # Use routing_key: gateway doesn't know about -hybrid
                success = await gateway.client.load_model(model_id.routing_key)

                if not success:
                    logger.error(
                        f"❌ Gateway load initiation failed for {model_id.routing_key}"
                    )
                    raise HTTPException(
                        500, f"Failed to initiate load for model {model_id}"
                    )

                # Wait for outcome event (MODEL_LOADED or MODEL_LOAD_FAILED)
                logger.info(
                    f"⏳ Waiting for model {model_id.routing_key} load outcome "
                    "(event-driven)..."
                )
                timeout = 180.0
                loop = asyncio.get_running_loop()
                start_time = loop.time()

                try:
                    await asyncio.wait_for(tracker.future, timeout=timeout)
                except TimeoutError:
                    elapsed = loop.time() - start_time
                    logger.error(
                        f"❌ Timeout waiting for {model_id.routing_key} load outcome "
                        f"after {elapsed:.1f}s (budget {timeout}s)"
                    )
                    raise HTTPException(
                        504,
                        f"Model load outcome timed out after {elapsed:.1f}s "
                        f"(budget {timeout}s)",
                    ) from None

                elapsed = loop.time() - start_time
                logger.info(
                    f"✅ Model {model_id.routing_key} loaded on Gateway "
                    f"(took {elapsed:.1f}s)"
                )
                return ModelLoadResponse(
                    status="ok",
                    model_id=model_id.routing_key,
                    message="Model ready on gateway",
                )

            except LoadFailedError as e:
                # Load failed - return immediately with error
                logger.warning(
                    f"⚠️ Model {model_id.routing_key} load failed: {e.message}"
                )
                return ModelLoadResponse(
                    status="failed",
                    model_id=model_id.routing_key,
                    message=e.message,
                )

            finally:
                # CRITICAL: Always unregister callbacks
                tracker.unregister()

        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"❌ Gateway connection error loading {model_id!r}")
            raise HTTPException(503, f"Gateway connection failed: {e}")

    @router.post("/unload", response_model=ModelUnloadResponse)
    async def unload_model(body: ModelUnloadRequest) -> ModelUnloadResponse:
        """
        Unload a model from local Gateway.

        Called by Master as part of eviction execution.
        Idempotent: returns success if model not loaded.

        Relay Topology:
            Remote → Edge (via Unix socket) → Gateway

        Direct Topology:
            Remote → Gateway
        """
        model_id = ModelId.parse(body.model_id)
        logger.info(f"🗑️ Received unload command from Master: {model_id!r}")

        # Relay topology: Forward to Edge via Unix socket
        if local_edge_client:
            logger.info(f"📡 Forwarding unload to Edge via Unix socket: {model_id!r}")
            try:
                client, headers = _edge_client_and_headers(60.0)
                async with client:
                    response = await client.post(
                        "http://edge/api/v1/federation/models/unload",
                        json={"model_id": model_id.synthetic_id},
                        headers=headers,
                    )
                    response.raise_for_status()
                    data = response.json()
                    logger.info(f"✅ Edge unload response: {data}")
                    return ModelUnloadResponse(**data)
            except httpx.HTTPStatusError as e:
                logger.error(f"❌ Edge unload failed: HTTP {e.response.status_code}")
                raise HTTPException(
                    status_code=e.response.status_code,
                    detail=f"Edge unload failed: {e.response.text}",
                )
            except Exception as e:
                logger.exception(f"❌ Failed to forward unload to Edge: {e}")
                raise HTTPException(503, f"Edge connection failed: {e}")

        # Direct topology: Use local gateway_manager
        if not gateway_manager:
            logger.error("❌ Gateway manager not available and no Edge client")
            raise HTTPException(
                status_code=503,
                detail="Gateway manager unavailable",
            )

        try:
            gateway = gateway_manager.require_gateway()

            # Rely on gateway.client.unload_model idempotency (avoid TOCTOU)
            success = await gateway.client.unload_model(model_id.routing_key)

            if success:
                logger.info(f"✅ Model {model_id.routing_key} unloaded from Gateway")
                return ModelUnloadResponse(
                    status="ok",
                    model_id=model_id.routing_key,
                    message="Model unloaded",
                )
            else:
                logger.error(f"❌ Gateway unload failed for {model_id.routing_key}")
                return ModelUnloadResponse(
                    status="failed",
                    model_id=model_id.routing_key,
                    message="Gateway unload failed",
                )

        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"❌ Gateway error unloading {model_id!r}")
            raise HTTPException(503, f"Gateway connection failed: {e}")

    return router
