"""Lifecycle management components for Stargate."""

from .shutdown import initialize_shutdown_handler, register_gateways_with_tracker

__all__ = ["initialize_shutdown_handler", "register_gateways_with_tracker"]
