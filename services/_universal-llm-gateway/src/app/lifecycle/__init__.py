"""Public facade for the Universal LLM Gateway application lifecycle package.

This package encapsulates all startup, runtime, and shutdown orchestration for the
FastAPI application. Only the `lifespan` async context manager is part of the public
API and is re-exported here so that existing imports continue to work without
modification:

    from .lifecycle import lifespan
    from src.app.lifecycle import lifespan

All other modules in this package (logging_bootstrap, component_bootstrap,
model_validation_startup, worker_runtime_startup, hot_reload_runtime,
edge_service_runtime, shutdown_sequence, fastapi_lifespan) are internal
implementation details and should not be imported directly by consumers.
The original monolithic lifecycle.py has been split for maintainability while
preserving behavioral equivalence and import compatibility.
"""

from .fastapi_lifespan import lifespan

__all__ = ["lifespan"]
