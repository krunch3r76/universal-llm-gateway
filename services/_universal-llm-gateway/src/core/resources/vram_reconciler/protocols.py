"""Structural typing protocols for VramReconciler dependency injection."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from ..types import ModelResourceInfo


class ResourceTrackerProto(Protocol):
    """Resource tracker surface used by reconciliation sweeps."""

    _models: dict[str, ModelResourceInfo]

    def get_loaded_models(self) -> list[str]: ...

    async def get_system_resources(self) -> dict[str, Any]: ...

    def set_model_not_loaded(self, model_id: str, reason: str) -> None: ...


class UnloadResultProto(Protocol):
    """Minimal unload outcome shape returned by the worker controller."""

    success: bool
    reason: str | None


class WorkerControllerProto(Protocol):
    """Worker controller surface for process introspection and forced unload."""

    def get_running_worker_processes(self) -> dict[str, int]: ...

    def get_engine_pid(self, model_id: str) -> int | None: ...

    async def check_engine_health(self, model_id: str) -> bool: ...

    async def unload_model(
        self, model_id: str, force: bool = False
    ) -> UnloadResultProto: ...


class EventBusProto(Protocol):
    """Fire-and-forget event bus used to publish reconciliation signals."""

    async def publish_nowait(self, event: object) -> None: ...
