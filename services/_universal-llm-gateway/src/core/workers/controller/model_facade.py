"""Model load/unload facade mixin for WorkerController."""

from ..model_operations import UnloadResult


class ModelFacadeMixin:
    """Delegates model loading and unloading to loader/unloader collaborators."""

    async def load_model(self, model_id: str) -> bool:
        return await self._model_loader.load_model(model_id)

    async def ensure_model_loaded(
        self, model_id: str, correlation_id: str | None = None
    ) -> bool:
        return await self._model_loader.ensure_model_loaded(model_id, correlation_id)

    async def unload_model(self, model_id: str, force: bool = False) -> UnloadResult:
        """Unload a model. Returns UnloadResult with success/skip status.

        Args:
            model_id: Model to unload
            force: If True, kill process immediately bypassing busy check
        """
        return await self._model_unloader.unload_model(model_id, force=force)

    async def unload_current_model(self) -> UnloadResult:
        """Unload current model. Returns UnloadResult with success/skip status."""
        return await self._model_unloader.unload_current_model()
