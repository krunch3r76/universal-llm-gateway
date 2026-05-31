"""WebSocket transport for telemetry (event-driven)."""

# Lazy imports to avoid circular dependencies
# Import from submodules directly when needed


def __getattr__(name: str):
    """Lazy module loading to avoid circular import issues."""
    if name == "WSMasterReceiver":
        from .master.server import WSMasterReceiver

        return WSMasterReceiver
    elif name == "WSRemoteClient":
        from .remote.client import WSRemoteClient

        return WSRemoteClient
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["WSMasterReceiver", "WSRemoteClient"]
