"""
Package-shadow exports for federated routing orchestration entrypoints.

This package preserves the original public import surface while splitting
selection, admission, and finalization concerns into focused modules.
"""

from .entrypoint import _route_to_federated_gateway

__all__ = ["_route_to_federated_gateway"]
