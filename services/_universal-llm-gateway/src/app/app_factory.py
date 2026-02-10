"""Application factory for creating FastAPI application instances."""

import yaml
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import Response

try:
    from .. import __version__

    # Import path-based routers
    from ..core.config_loader import get_config_loader
    from ..routers import health, metrics
    from ..routers.api.v1 import metrics as api_metrics
    from ..routers.api.v1.catalog import get as catalog_get
    from ..routers.api.v1.config import reload as config_reload
    from ..routers.api.v1.jobs import router as jobs_router
    from ..routers.api.v1.management import cancellation as cancellation_management
    from ..routers.api.v1.model_info import aliases, configurations, stats, validate
    from ..routers.api.v1.model_info._model_id_ import get as model_info_get
    from ..routers.api.v1.models import management as model_management
    from ..routers.api.v1.models import requirements
    from ..routers.api.v1.models._model_id_ import cleanup as api_model_cleanup
    from ..routers.api.v1.models._model_id_ import delete as api_model_delete
    from ..routers.api.v1.models._model_id_ import load as api_model_load
    from ..routers.api.v1.status import detailed, resources
    from ..routers.api.v1.tokens import count
    from ..routers.middleware import setup_all_middleware
    from ..routers.v1.audio import stream as audio_stream
    from ..routers.v1.audio import transcriptions as audio_transcriptions
    from ..routers.v1.chat import completions
    from ..routers.v1 import embeddings
    from ..routers.v1.images import generations as image_generations
    from ..routers.v1.models import extended as models_extended
    from ..routers.v1.models import get as models_get
    from ..routers.v1.models._model_id_ import extended as model_extended
    from ..routers.v1.models._model_id_ import get as model_get
    from ..routers.ws import stargate_router, state_router
    from .lifecycle import lifespan
except ImportError:
    # When running directly, use absolute imports
    from src import __version__
    from src.app.lifecycle import lifespan

    # Import path-based routers
    from src.core.config_loader import get_config_loader
    from src.routers import health, metrics
    from src.routers.api.v1 import metrics as api_metrics
    from src.routers.api.v1.catalog import get as catalog_get
    from src.routers.api.v1.config import reload as config_reload
    from src.routers.api.v1.jobs import router as jobs_router
    from src.routers.api.v1.management import cancellation as cancellation_management
    from src.routers.api.v1.model_info import aliases, configurations, stats, validate
    from src.routers.api.v1.model_info._model_id_ import get as model_info_get
    from src.routers.api.v1.models import management as model_management
    from src.routers.api.v1.models import requirements
    from src.routers.api.v1.models._model_id_ import cleanup as api_model_cleanup
    from src.routers.api.v1.models._model_id_ import delete as api_model_delete
    from src.routers.api.v1.models._model_id_ import load as api_model_load
    from src.routers.api.v1.status import detailed, resources
    from src.routers.api.v1.tokens import count
    from src.routers.middleware import setup_all_middleware
    from src.routers.v1.audio import stream as audio_stream
    from src.routers.v1.audio import transcriptions as audio_transcriptions
    from src.routers.v1.chat import completions
    from src.routers.v1 import embeddings
    from src.routers.v1.images import generations as image_generations
    from src.routers.v1.models import extended as models_extended
    from src.routers.v1.models import get as models_get
    from src.routers.v1.models._model_id_ import extended as model_extended
    from src.routers.v1.models._model_id_ import get as model_get
    from src.routers.ws import stargate_router, state_router


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""

    app = FastAPI(
        title="Universal LLM Gateway API",
        description="""
        OpenAI-compatible API gateway with mandatory process isolation for complete CUDA context cleanup.

        ## Process Isolation Architecture

        - **Mandatory Process Isolation**: Each model runs in a separate subprocess for complete CUDA context isolation
        - **Complete CUDA Cleanup**: Process termination ensures perfect GPU memory cleanup between model switches
        - **Zero Gateway Downtime**: Main process continues serving while worker processes are switched
        - **Unix Socket IPC**: High-performance local communication between gateway and workers
        - **Automatic Recovery**: Failed worker processes are automatically restarted
        - **Health Monitoring**: Continuous monitoring of worker process health with auto-recovery

        ## Model Support

        - **GGUF Support**: Local models via llama-cpp-python with RTX 5090 optimization
        - **GPTQ Support**: Quantized models via auto-gptq with device mapping
        - **AWQ Support**: AWQ quantized models via transformers + autoawq with RTX 50xx support
        - **API Proxy**: OpenAI and Anthropic models via API proxy
        - **Streaming Responses**: Real-time streaming chat completions
        - **Memory Optimization**: Smart VRAM usage optimization for RTX 5090
        - **Single Model Focus**: One model loaded at a time for maximum performance and stability

        ## OpenAI Compatibility

        - **Full API Compatibility**: 100% compatible with OpenAI chat completion API
        - **Query Parameter Model Selection**: Override model via URL parameter
        - **Streaming Support**: Server-Sent Events for real-time responses
        - **Complete Error Handling**: Proper HTTP status codes and error messages

        ## Model Selection Priority

        1. Query parameter: `?model=model_name` (highest priority)
        2. Request body `model` field

        ## Supported Model Formats

        - **GGUF**: Quantized models using llama-cpp-python (e.g., Q4_K_M, Q8_0)
        - **GPTQ**: Auto-GPTQ quantized models with 4-bit precision
        - **AWQ**: AWQ quantized models using transformers + autoawq with RTX 50xx support
        - **OpenAI API**: Proxy to OpenAI models (gpt-4o, gpt-4o-mini)
        - **Anthropic API**: Proxy to Claude models (claude-3-5-sonnet)

        """,
        version=__version__,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    # Setup middleware with default config (will be overridden in lifespan)
    try:
        config_loader = get_config_loader()
        gateway_config, _, _ = config_loader.load_all_configs()
        setup_all_middleware(app, gateway_config)
    except Exception:
        # Fallback to basic middleware setup if config loading fails
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=False,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    # Include path-based routers directly
    # Root endpoints
    app.include_router(health.router)
    app.include_router(metrics.router)

    # v1 endpoints
    app.include_router(completions.router, prefix="/v1")
    app.include_router(embeddings.router, prefix="/v1", tags=["OpenAI Compatible"])
    app.include_router(models_get.router, prefix="/v1")
    app.include_router(models_extended.router, prefix="/v1")
    app.include_router(model_get.router, prefix="/v1")
    app.include_router(model_extended.router, prefix="/v1")

    # Audio streaming WebSocket endpoint
    app.include_router(audio_stream.router, prefix="/v1", tags=["Audio Streaming"])

    # Audio file transcription endpoint (OpenAI-compatible)
    app.include_router(
        audio_transcriptions.router, prefix="/v1", tags=["Audio Transcription"]
    )

    # Image generation endpoint (OpenAI-compatible)
    app.include_router(
        image_generations.router, prefix="/v1", tags=["Image Generation"]
    )

    # api/v1 endpoints (routers have /v1/... prefixes, so add /api prefix)
    app.include_router(resources.router, prefix="/api")
    app.include_router(detailed.router, prefix="/api")
    app.include_router(count.router, prefix="/api")
    app.include_router(stats.router, prefix="/api")
    app.include_router(aliases.router, prefix="/api")
    app.include_router(validate.router, prefix="/api")
    app.include_router(configurations.router, prefix="/api")
    app.include_router(model_info_get.router, prefix="/api")
    app.include_router(api_model_load.router, prefix="/api")
    app.include_router(api_model_delete.router, prefix="/api")
    app.include_router(api_model_cleanup.router, prefix="/api")
    app.include_router(requirements.router, prefix="/api")

    # Configuration hot reload API
    app.include_router(
        config_reload.router, prefix="/api/v1/config", tags=["Configuration Hot Reload"]
    )

    # Model management API (conditionally included based on ENABLE_MANAGEMENT_API env var)
    # Security is enforced at the router level via dependencies
    app.include_router(
        model_management.router, prefix="/api", tags=["Model Management"]
    )

    # Streaming cancellation management API
    app.include_router(
        cancellation_management.router, prefix="/api", tags=["Streaming Management"]
    )

    # Observability metrics API (router already has /api/v1/metrics prefix)
    app.include_router(api_metrics.router, tags=["Observability"])

    # WebSocket state streaming API
    app.include_router(state_router, prefix="/api/v1", tags=["State Streaming"])

    # WebSocket Stargate control plane API
    app.include_router(stargate_router, tags=["Stargate Control Plane"])

    # Model catalog API
    app.include_router(catalog_get.router, prefix="/api", tags=["Model Catalog"])

    # Jobs API (background task execution)
    app.include_router(jobs_router, prefix="/api", tags=["Jobs"])

    # Custom OpenAPI YAML endpoint
    @app.get("/openapi.yaml", include_in_schema=False)
    async def get_openapi_yaml():
        """Get OpenAPI specification in YAML format"""
        openapi_schema = get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            routes=app.routes,
        )
        yaml_schema = yaml.dump(openapi_schema, default_flow_style=False)
        return Response(content=yaml_schema, media_type="application/yaml")

    # Root endpoint
    @app.get("/", include_in_schema=False)
    async def root():
        """Root endpoint with basic information"""
        return {
            "message": "Universal LLM Gateway API",
            "version": __version__,
            "docs": "/docs",
            "openapi": "/openapi.json",
            "health": "/health",
            "models": "/v1/models",
        }

    return app
