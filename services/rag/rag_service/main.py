"""RAG service application assembly.

This entrypoint wires lifecycle hooks and routers, then exposes the FastAPI app
used by deployment scripts. Runtime state remains centralized in ``state``.
"""

from __future__ import annotations

from fastapi import FastAPI
from universal_event_bus import EventBus

from . import api, lifecycle, state

app = FastAPI(title="RAG Service")


@app.on_event("startup")
async def _startup() -> None:
    """Initialize all RAG subsystems before serving requests."""
    await lifecycle._startup()


@app.on_event("shutdown")
async def _shutdown() -> None:
    """Stop background services and release resources cleanly."""
    await lifecycle._shutdown()


def get_event_bus() -> EventBus | None:
    """Return the active event bus after startup, or None before initialization."""
    return state.get_event_bus()


app.include_router(api.router)
