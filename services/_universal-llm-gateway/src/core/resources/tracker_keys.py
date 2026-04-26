"""Tracking-key helpers shared by ResourceTracker split modules."""

from __future__ import annotations

from model_id import ModelId


def _tracking_key(model_id: str | ModelId) -> str:
    """Per-variant key preserving -hybrid for state machines and ModelResourceInfo."""
    if isinstance(model_id, ModelId):
        return model_id.tracking_key
    return ModelId.parse(model_id).tracking_key


def _process_key(model_id: str | ModelId) -> str:
    """Shared-process key stripping -hybrid for supervisor/socket/PID lookups."""
    if isinstance(model_id, ModelId):
        return model_id.process_key
    return ModelId.parse(model_id).process_key
