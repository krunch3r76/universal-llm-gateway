"""Thin wrapper - delegates to libs/agent_bus_store."""

from agent_bus_store.server import create_app

app = create_app()
