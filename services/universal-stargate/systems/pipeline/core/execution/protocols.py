"""
Protocols for execution domain type safety.

These protocols define interfaces for injected dependencies,
enabling type checking without importing concrete implementations.
"""

from typing import Protocol


class BatchRouterProtocol(Protocol):
    """
    Generic batch routing interface for domain isolation.

    This protocol defines the contract for batch routers without
    requiring imports from the proxy domain. Enables dependency
    injection with compile-time type safety.
    """

    async def route_batch_dict(self, batch_data: dict) -> dict:
        """
        Route batch specified as generic dict.

        Args:
            batch_data: Dict with keys:
                - 'batch_id': str
                - 'requests': list[dict] with request_id, model_id, vram_mb, ram_mb
                - 'pipeline_id': str
                - 'total_vram_mb': int
                - 'total_ram_mb': int

        Returns:
            Dict with keys:
                - 'gateway_assignments': dict[request_id, gateway_name]
                - 'deferred_requests': list[request_id]
        """
        ...
