"""
Checkpoint manager for pipeline execution.

Handles checkpoint save/load with event emission.
"""

import hashlib
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ...handlers.protocol import StepOutput
    from ...schemas import CheckpointConfig, StepConfig

from .backend import CheckpointBackend, CheckpointData

logger = logging.getLogger(__name__)


class CheckpointManager:
    """
    Manages checkpoint storage and event emission.

    Key generation:
        {pipeline_id}:{step_name}:{execution_id}

    Optional input fingerprint for cross-execution reuse:
        {pipeline_id}:{step_name}:{input_fingerprint}
    """

    def __init__(
        self,
        backend: CheckpointBackend,
        config: "CheckpointConfig",
        pipeline_id: str,
        execution_id: str,
        event_bus: Any = None,
    ):
        self._backend = backend
        self._config = config
        self._pipeline_id = pipeline_id
        self._execution_id = execution_id
        self._event_bus = event_bus

    def should_checkpoint_step(self, step_name: str) -> bool:
        """Check if step should be checkpointed based on config strategy."""
        if not self._config.enabled:
            return False
        return self._config.strategy == "per_step"

    def _generate_key(
        self,
        step_name: str,
        input_fingerprint: str | None = None,
    ) -> str:
        """
        Generate checkpoint key.

        Default: execution-scoped (no cross-execution reuse)
        With fingerprint: input-scoped (can resume from different execution)
        """
        if input_fingerprint:
            return f"{self._pipeline_id}:{step_name}:{input_fingerprint}"
        else:
            return f"{self._pipeline_id}:{step_name}:{self._execution_id}"

    async def load_checkpoint(
        self,
        step_name: str,
        input_fingerprint: str | None = None,
    ) -> CheckpointData | None:
        """
        Load checkpoint for step.

        Emits CheckpointLoaded event on success.
        Returns None if not found or disabled.
        """
        if not self._config.enabled:
            return None

        key = self._generate_key(step_name, input_fingerprint)

        try:
            data = await self._backend.load(key)
        except Exception as e:
            logger.warning("Failed to load checkpoint %s: %s", key, e)
            await self._emit_failed(step_name, "load", str(e))
            return None

        if data:
            if data.checksum and self._config.options.get("verify_checksums", True):
                computed = hashlib.sha256(data.output_raw.encode()).hexdigest()
                if computed != data.checksum:
                    logger.error("Checkpoint checksum mismatch for %s", key)
                    await self._emit_failed(step_name, "load", "checksum_mismatch")
                    return None

            logger.info("Resuming step '%s' from checkpoint", step_name)
            await self._emit_loaded(step_name, key, data.saved_at)

        return data

    async def save_checkpoint(
        self,
        step_name: str,
        output: "StepOutput",
        input_fingerprint: str | None = None,
    ) -> None:
        """
        Save checkpoint for step.

        Emits CheckpointSaved event on success.
        """
        if not self._config.enabled:
            return

        key = self._generate_key(step_name, input_fingerprint)

        # Use StepOutput's checkpoint extraction
        output_raw, output_json, output_meta = output.to_checkpoint_data()

        checksum = None
        if self._config.options.get("enable_checksums", False):
            checksum = hashlib.sha256(output_raw.encode()).hexdigest()

        data = CheckpointData(
            step_name=step_name,
            inputs_fingerprint=input_fingerprint or "",
            output_raw=output_raw,
            output_json=output_json,
            output_meta=output_meta,
            saved_at=datetime.now(UTC).isoformat(),
            pipeline_version=self._config.options.get("version", "1.0"),
            checksum=checksum,
        )

        try:
            await self._backend.save(key, data)
            logger.info("Saved checkpoint for step '%s'", step_name)
            await self._emit_saved(step_name, key)
        except Exception as e:
            logger.error("Failed to save checkpoint %s: %s", key, e)
            await self._emit_failed(step_name, "save", str(e))

    def should_checkpoint(self, step: "StepConfig") -> bool:
        """Determine if step should be checkpointed."""
        if not self._config.enabled:
            return False

        if step.checkpoint is False:
            return False
        if step.checkpoint is True:
            return True
        if step.checkpoint == "milestone":
            return self._config.strategy in ("milestone", "per_step")

        return self._config.strategy == "per_step"

    async def _emit_event(self, event_factory_name: str, **kwargs) -> None:
        """
        Generic event emission helper.

        Args:
            event_factory_name: Name of event factory function
            **kwargs: Event-specific parameters
        """
        if self._event_bus is None:
            return

        from ... import events

        # Get event factory function by name
        event_factory = getattr(events, event_factory_name)

        # Add common fields
        event = event_factory(
            pipeline_id=self._pipeline_id, execution_id=self._execution_id, **kwargs
        )
        await self._event_bus.publish_nowait(event)

    async def _emit_saved(self, step_name: str, key: str) -> None:
        """Emit CheckpointSaved event."""
        await self._emit_event(
            "CheckpointSaved",
            step_name=step_name,
            checkpoint_key=key,
            storage_backend=self._backend.backend_name,
        )

    async def _emit_loaded(self, step_name: str, key: str, saved_at: str) -> None:
        """Emit CheckpointLoaded event."""
        await self._emit_event(
            "CheckpointLoaded",
            step_name=step_name,
            checkpoint_key=key,
            storage_backend=self._backend.backend_name,
            saved_at=saved_at,
        )

    async def _emit_failed(self, step_name: str, operation: str, error: str) -> None:
        """Emit CheckpointFailed event."""
        await self._emit_event(
            "CheckpointFailed",
            step_name=step_name,
            operation=operation,
            error=error,
        )
